"""One-shot OS-image observation for the fixed Linux/macOS Python runtime.

The current process image is the only selector. Normal Python invocation links
and macOS framework launchers do not select a second executable here.
"""

from __future__ import annotations

import ctypes
import os
import sys
import unicodedata
from pathlib import Path

from task_governance_tool.state_paths import (
    StatePathError,
    inspect_physical_directory,
    inspect_physical_file,
)
from task_governance_tool.verification_runner_runtime import (
    VerificationRunnerRuntimeError,
)


_MACOS_IMAGE_BUFFER_BYTES = 4096  # PROC_PIDPATHINFO_MAXSIZE


def _runtime_unavailable() -> VerificationRunnerRuntimeError:
    return VerificationRunnerRuntimeError(
        "runtime_unavailable", "the fixed package runtime could not be verified"
    )


def _bounded_absolute_path(value: object) -> Path:
    if type(value) is not str:
        raise _runtime_unavailable()
    try:
        if (
            not 1 <= len(value.encode("utf-8")) <= 4096
            or any(unicodedata.category(character) == "Cc" for character in value)
            or not os.path.isabs(value)
            or os.path.normpath(value) != value
        ):
            raise _runtime_unavailable()
        return Path(value)
    except UnicodeError as exc:
        raise _runtime_unavailable() from exc


def _linux_process_image() -> str:
    try:
        image = os.readlink("/proc/self/exe")
        if image.endswith(" (deleted)"):
            raise _runtime_unavailable()
        return image
    except OSError as exc:
        raise _runtime_unavailable() from exc


def _macos_process_image() -> str:
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        library.proc_pidpath.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)
        library.proc_pidpath.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(_MACOS_IMAGE_BUFFER_BYTES)
        count = int(library.proc_pidpath(os.getpid(), buffer, len(buffer)))
        if not 0 < count < len(buffer) or len(buffer.value) != count:
            raise _runtime_unavailable()
        return buffer.value.decode("utf-8", errors="strict")
    except (AttributeError, OSError, RuntimeError, UnicodeError) as exc:
        raise _runtime_unavailable() from exc


def _observe_parent_process_executable() -> Path:
    if os.name != "posix":
        raise _runtime_unavailable()
    if sys.platform == "linux":
        value = _linux_process_image()
    elif sys.platform == "darwin":
        value = _macos_process_image()
    else:
        raise _runtime_unavailable()
    return _bounded_absolute_path(value)


def observe_fixed_package_runtime(materialized_root: Path, scratch_root: Path) -> Path:
    """Return only the physical current image; retain no file lease or handle."""

    try:
        target = _bounded_absolute_path(os.fspath(materialized_root))
        scratch = _bounded_absolute_path(os.fspath(scratch_root))
        if (
            target.name != "target"
            or scratch.name != "scratch"
            or target.parent != scratch.parent
        ):
            raise _runtime_unavailable()
        inspect_physical_directory(target, root=Path(target.anchor))
        inspect_physical_directory(scratch, root=Path(scratch.anchor))
        executable = _observe_parent_process_executable()
        if executable.is_relative_to(target) or executable.is_relative_to(scratch):
            raise _runtime_unavailable()
        inspect_physical_file(executable, root=Path(executable.anchor))
        return executable
    except VerificationRunnerRuntimeError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, StatePathError) as exc:
        raise _runtime_unavailable() from exc


__all__ = ["observe_fixed_package_runtime"]
