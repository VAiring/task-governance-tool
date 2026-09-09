"""One-shot observation of the fixed Windows parent executable.

The service receives one verified absolute Path without retaining a file handle.
Actual process cleanup acceptance remains owned by the service and adapter.
"""

from __future__ import annotations

import ctypes
import os
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any

from task_governance_tool.verification_runner_runtime import (
    VerificationRunnerRuntimeError,
)


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_MAX_WINDOWS_PATH = 32_768


def _runtime_unavailable() -> VerificationRunnerRuntimeError:
    return VerificationRunnerRuntimeError(
        "runtime_unavailable",
        "the fixed package runtime could not be verified",
    )


def _kernel32() -> Any:
    if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
        raise _runtime_unavailable()
    try:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        library.GetModuleFileNameW.argtypes = (
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        )
        library.GetModuleFileNameW.restype = ctypes.c_uint32
        return library
    except VerificationRunnerRuntimeError:
        raise
    except (AttributeError, OSError, RuntimeError) as exc:
        raise _runtime_unavailable() from exc


def _is_reparse(details: os.stat_result, path: Path) -> bool:
    junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(details.st_mode)
        or bool(
            int(getattr(details, "st_file_attributes", 0))
            & _FILE_ATTRIBUTE_REPARSE_POINT
        )
        or bool(junction is not None and junction())
    )


def _bounded_absolute_path(value: object) -> Path:
    if type(value) is not str:
        raise _runtime_unavailable()
    try:
        encoded = value.encode("utf-8")
        utf16_units = len(value.encode("utf-16-le")) // 2
    except UnicodeError as exc:
        raise _runtime_unavailable() from exc
    if (
        not value
        or len(encoded) > 4096
        or not 1 <= utf16_units <= 4096
        or any(unicodedata.category(character) == "Cc" for character in value)
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
    ):
        raise _runtime_unavailable()
    path = Path(value)
    if any(part in {".", ".."} for part in path.parts):
        raise _runtime_unavailable()
    return path


def _observe_physical_path(
    path: Path,
    *,
    directory: bool,
) -> None:
    try:
        anchor = Path(path.anchor)
        if not path.anchor:
            raise _runtime_unavailable()
        current = anchor
        chain = [anchor]
        for component in path.parts[1:]:
            current /= component
            chain.append(current)
        for index, candidate in enumerate(chain):
            details = candidate.lstat()
            if _is_reparse(details, candidate):
                raise _runtime_unavailable()
            final = index == len(chain) - 1
            if final and not directory and not stat.S_ISREG(details.st_mode):
                raise _runtime_unavailable()
            if (not final or directory) and not stat.S_ISDIR(details.st_mode):
                raise _runtime_unavailable()
    except VerificationRunnerRuntimeError:
        raise
    except (OSError, RuntimeError) as exc:
        raise _runtime_unavailable() from exc


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((_path_key(path), _path_key(root))) == _path_key(root)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            return False
        raise _runtime_unavailable() from exc


def _observe_parent_process_executable() -> Path:
    try:
        buffer = ctypes.create_unicode_buffer(_MAX_WINDOWS_PATH)
        count = int(
            _kernel32().GetModuleFileNameW(
                None,
                buffer,
                _MAX_WINDOWS_PATH,
            )
        )
        if count <= 0 or count >= _MAX_WINDOWS_PATH:
            raise _runtime_unavailable()
        observed = _bounded_absolute_path(buffer.value)
        declared = _bounded_absolute_path(sys.executable)
        _observe_physical_path(observed, directory=False)
        _observe_physical_path(declared, directory=False)
        if not os.path.samestat(observed.lstat(), declared.lstat()):
            raise _runtime_unavailable()
        _observe_physical_path(observed, directory=False)
        _observe_physical_path(declared, directory=False)
        return observed
    except VerificationRunnerRuntimeError:
        raise
    except (AttributeError, OSError, RuntimeError, UnicodeError) as exc:
        raise _runtime_unavailable() from exc


def observe_fixed_package_runtime(
    materialized_root: str | os.PathLike[str],
    scratch_root: str | os.PathLike[str],
) -> Path:
    """Observe the fixed runtime without retaining an executable-file lease."""

    try:
        target = _bounded_absolute_path(os.fspath(materialized_root))
        scratch = _bounded_absolute_path(os.fspath(scratch_root))
        if (
            target.name != "target"
            or scratch.name != "scratch"
            or _path_key(target.parent) != _path_key(scratch.parent)
            or _path_key(target) == _path_key(scratch)
        ):
            raise _runtime_unavailable()
        _observe_physical_path(target, directory=True)
        _observe_physical_path(scratch, directory=True)
        executable = _observe_parent_process_executable()
        if executable.name.casefold() != "python.exe":
            raise _runtime_unavailable()
        _observe_physical_path(executable, directory=False)
        if _is_beneath(executable, target) or _is_beneath(executable, scratch):
            raise _runtime_unavailable()
        return executable
    except VerificationRunnerRuntimeError:
        raise
    except (OSError, RuntimeError, TypeError, UnicodeError) as exc:
        raise _runtime_unavailable() from exc


__all__ = ["observe_fixed_package_runtime"]
