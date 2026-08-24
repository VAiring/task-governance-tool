"""Release-bound Runner implementation identity."""

from __future__ import annotations

import ctypes
import os
import stat
import sys
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool import __version__
from task_governance_tool.evidence_ledger import domain_digest
from task_governance_tool.self_status import (
    ReleaseManifestVerificationError,
    verify_release_manifest_core,
)
from task_governance_tool.verification_runner import RUNNER_IMPLEMENTATION_VERSION


RUNNER_IMPLEMENTATION_DIGEST_DOMAIN = (
    b"taskgov-verification-runner-implementation-v1\0"
)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_HANDLE_FLAG_INHERIT = 0x00000001
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_MAX_WINDOWS_PATH = 32_768


class _FILETIME(ctypes.Structure):
    _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("attributes", ctypes.c_uint32),
        ("creation_time", _FILETIME),
        ("last_access_time", _FILETIME),
        ("last_write_time", _FILETIME),
        ("volume_serial", ctypes.c_uint32),
        ("size_high", ctypes.c_uint32),
        ("size_low", ctypes.c_uint32),
        ("link_count", ctypes.c_uint32),
        ("file_index_high", ctypes.c_uint32),
        ("file_index_low", ctypes.c_uint32),
    )


@dataclass(frozen=True)
class _RuntimeFileIdentity:
    volume_serial: int
    file_index_high: int
    file_index_low: int
    size_high: int
    size_low: int
    last_write_high: int
    last_write_low: int


@dataclass(frozen=True)
class VerificationRunnerRuntimeError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _policy_mismatch() -> VerificationRunnerRuntimeError:
    return VerificationRunnerRuntimeError(
        "policy_mismatch",
        "the installed Runner implementation does not match its release manifest",
    )


def _runtime_unavailable() -> VerificationRunnerRuntimeError:
    return VerificationRunnerRuntimeError(
        "runtime_unavailable",
        "the fixed package runtime could not be verified",
    )


@dataclass(frozen=True)
class RunnerImplementationIdentity:
    implementation_version: str
    implementation_digest: str
    manifest_version: int
    package_name: str
    package_version: str
    core_files: tuple[tuple[str, str], ...]

    def canonical_value(self) -> dict[str, Any]:
        return {
            "core_files": dict(self.core_files),
            "manifest_version": self.manifest_version,
            "package_name": self.package_name,
            "package_version": self.package_version,
        }


class RunnerFixedExecutableLease:
    """A non-inheritable executable handle held across bounded process use."""

    __slots__ = (
        "_executable",
        "_handle",
        "_identity",
        "_state",
        "_lock",
        "_context_active",
    )

    def __init__(
        self,
        executable: Path,
        handle: int,
        identity: _RuntimeFileIdentity,
    ) -> None:
        self._executable = Path(executable)
        self._handle = int(handle)
        self._identity = identity
        self._state = "open"
        self._lock = threading.Lock()
        self._context_active = False

    def __repr__(self) -> str:
        return "RunnerFixedExecutableLease(closed=%r)" % self.closed

    @property
    def executable(self) -> Path:
        with self._lock:
            if self._state != "open" or self._handle == 0:
                raise _runtime_unavailable()
            return self._executable

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._state == "closed"

    def _close_locked(self) -> None:
        if self._state == "closed":
            return
        if self._state != "open" or self._handle == 0:
            raise _runtime_unavailable()
        handle = self._handle
        try:
            if not _kernel32().CloseHandle(ctypes.c_void_p(handle)):
                raise _runtime_unavailable()
            self._handle = 0
            self._state = "closed"
        except VerificationRunnerRuntimeError:
            raise
        except BaseException as exc:
            # The native call may already have closed and recycled the value.
            # Mark it uncertain and never retry it.
            self._handle = 0
            self._state = "uncertain"
            raise _runtime_unavailable() from exc

    def close(self) -> None:
        with self._lock:
            if self._state == "closed":
                return
            if self._context_active:
                raise _runtime_unavailable()
            self._close_locked()

    def __enter__(self) -> "RunnerFixedExecutableLease":
        with self._lock:
            if (
                self._state != "open"
                or self._handle == 0
                or self._context_active
            ):
                raise _runtime_unavailable()
            self._context_active = True
            return self

    def __exit__(self, *_args: object) -> None:
        with self._lock:
            if not self._context_active:
                raise _runtime_unavailable()
            self._context_active = False
            self._close_locked()


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
        library.CreateFileW.argtypes = (
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        )
        library.CreateFileW.restype = ctypes.c_void_p
        library.GetFileInformationByHandle.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
        )
        library.GetFileInformationByHandle.restype = ctypes.c_int
        library.GetHandleInformation.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        library.GetHandleInformation.restype = ctypes.c_int
        library.CloseHandle.argtypes = (ctypes.c_void_p,)
        library.CloseHandle.restype = ctypes.c_int
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


def _parent_process_executable() -> tuple[Path, int, _RuntimeFileIdentity]:
    observed_handle = 0
    declared_handle = 0
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
        observed_handle, observed_identity = _open_runtime_handle(observed)
        declared_handle, declared_identity = _open_runtime_handle(declared)
        _observe_physical_path(observed, directory=False)
        _observe_physical_path(declared, directory=False)
        if observed_identity != declared_identity:
            raise _runtime_unavailable()
        declared_close = _close_runtime_handle_once(declared_handle)
        if declared_close == "uncertain":
            declared_handle = 0
        if declared_close != "closed":
            raise _runtime_unavailable()
        declared_handle = 0
        leased_handle = observed_handle
        observed_handle = 0
        return observed, leased_handle, observed_identity
    except VerificationRunnerRuntimeError:
        raise
    except (AttributeError, OSError, RuntimeError, UnicodeError) as exc:
        raise _runtime_unavailable() from exc
    finally:
        cleanup_failed = False
        if declared_handle:
            if _close_runtime_handle_once(declared_handle) != "closed":
                cleanup_failed = True
        if observed_handle:
            if _close_runtime_handle_once(observed_handle) != "closed":
                cleanup_failed = True
        if cleanup_failed:
            raise _runtime_unavailable()


def _query_identity(handle: int) -> _RuntimeFileIdentity:
    information = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel32().GetFileInformationByHandle(
        ctypes.c_void_p(handle),
        ctypes.byref(information),
    ):
        raise _runtime_unavailable()
    if information.attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise _runtime_unavailable()
    return _RuntimeFileIdentity(
        volume_serial=int(information.volume_serial),
        file_index_high=int(information.file_index_high),
        file_index_low=int(information.file_index_low),
        size_high=int(information.size_high),
        size_low=int(information.size_low),
        last_write_high=int(information.last_write_time.high),
        last_write_low=int(information.last_write_time.low),
    )


def _runtime_handle_value(value: object) -> int:
    raw = getattr(value, "value", value)
    handle = int(raw or 0)
    return 0 if handle in {0, _INVALID_HANDLE_VALUE} else handle


def _close_runtime_handle_once(handle: int) -> str:
    if not handle:
        return "closed"
    try:
        return (
            "closed"
            if _kernel32().CloseHandle(ctypes.c_void_p(handle))
            else "open"
        )
    except BaseException:
        return "uncertain"


def _open_runtime_handle(path: Path) -> tuple[int, _RuntimeFileIdentity]:
    raw: object = 0
    handle = 0
    acquisition_uncertain = False
    try:
        try:
            raw = _kernel32().CreateFileW(
                str(path),
                _GENERIC_READ,
                _FILE_SHARE_READ,
                None,
                _OPEN_EXISTING,
                _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
        except BaseException:
            acquisition_uncertain = True
            raise
        handle = _runtime_handle_value(raw)
        if not handle:
            raise _runtime_unavailable()
        flags = ctypes.c_uint32()
        if not _kernel32().GetHandleInformation(
            ctypes.c_void_p(handle),
            ctypes.byref(flags),
        ) or flags.value & _HANDLE_FLAG_INHERIT:
            raise _runtime_unavailable()
        identity = _query_identity(handle)
        return handle, identity
    except BaseException:
        cleanup_handle = handle
        if not cleanup_handle:
            try:
                cleanup_handle = _runtime_handle_value(raw)
            except BaseException:
                raise _runtime_unavailable()
            if acquisition_uncertain and not cleanup_handle:
                raise _runtime_unavailable()
        if _close_runtime_handle_once(cleanup_handle) != "closed":
            raise _runtime_unavailable()
        raise


def _same_locked_path(path: Path, expected: _RuntimeFileIdentity) -> bool:
    second = 0
    try:
        second, observed = _open_runtime_handle(path)
        close_state = _close_runtime_handle_once(second)
        if close_state == "closed":
            second = 0
            return observed == expected
        if close_state == "uncertain":
            second = 0
        return False
    except (VerificationRunnerRuntimeError, AttributeError, OSError, RuntimeError):
        return False
    finally:
        if second:
            _close_runtime_handle_once(second)


def open_fixed_package_runtime(
    materialized_root: str | os.PathLike[str],
    scratch_root: str | os.PathLike[str],
) -> RunnerFixedExecutableLease:
    """Open and hold the one fixed parent runtime without PATH resolution."""

    handle = 0
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
        executable, handle, identity = _parent_process_executable()
        if executable.name.casefold() != "python.exe":
            raise _runtime_unavailable()
        _observe_physical_path(executable, directory=False)
        if _is_beneath(executable, target) or _is_beneath(executable, scratch):
            raise _runtime_unavailable()
        if not _same_locked_path(executable, identity):
            raise _runtime_unavailable()
        lease = RunnerFixedExecutableLease(executable, handle, identity)
        handle = 0
        return lease
    except VerificationRunnerRuntimeError:
        raise
    except (OSError, RuntimeError, TypeError, UnicodeError) as exc:
        raise _runtime_unavailable() from exc
    finally:
        if handle:
            if _close_runtime_handle_once(handle) != "closed":
                raise _runtime_unavailable()


def capture_runner_implementation(
    skill_root: Path,
    *,
    expected_package_version: str | None = None,
) -> RunnerImplementationIdentity:
    """Bind the strict release manifest only after every core byte is verified."""

    try:
        manifest = verify_release_manifest_core(
            skill_root,
            expected_package_version=(
                __version__ if expected_package_version is None else expected_package_version
            ),
        )
    except ReleaseManifestVerificationError as exc:
        raise _policy_mismatch() from exc
    canonical = manifest.canonical_value()
    return RunnerImplementationIdentity(
        implementation_version=RUNNER_IMPLEMENTATION_VERSION,
        implementation_digest=domain_digest(
            RUNNER_IMPLEMENTATION_DIGEST_DOMAIN,
            canonical,
        ),
        manifest_version=manifest.manifest_version,
        package_name=manifest.package_name,
        package_version=manifest.package_version,
        core_files=tuple(sorted(manifest.core_files.items())),
    )


__all__ = [
    "RUNNER_IMPLEMENTATION_DIGEST_DOMAIN",
    "RunnerImplementationIdentity",
    "RunnerFixedExecutableLease",
    "VerificationRunnerRuntimeError",
    "capture_runner_implementation",
    "open_fixed_package_runtime",
]
