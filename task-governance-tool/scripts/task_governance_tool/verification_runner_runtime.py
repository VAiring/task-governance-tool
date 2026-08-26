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
from typing import Any, Literal

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


RuntimeHandleCleanupState = Literal["closed", "open", "uncertain"]
_RuntimeHandleSlotName = Literal["primary", "probe"]
_RuntimeHandlePhase = Literal[
    "empty",
    "acquiring",
    "open",
    "closing",
    "closed",
    "uncertain",
]
_RuntimeLeasePhase = Literal["new", "entering", "active", "exiting", "released"]


@dataclass
class _OwnedRuntimeHandle:
    handle: int = 0
    close_attempts: int = 0
    phase: _RuntimeHandlePhase = "empty"


def _merge_handle_cleanup_state(
    left: RuntimeHandleCleanupState,
    right: RuntimeHandleCleanupState,
) -> RuntimeHandleCleanupState:
    if "uncertain" in {left, right}:
        return "uncertain"
    return "open" if "open" in {left, right} else "closed"


@dataclass(frozen=True)
class VerificationRunnerRuntimeError(Exception):
    code: str
    message: str
    handle_cleanup_state: RuntimeHandleCleanupState = "uncertain"

    def __str__(self) -> str:
        return self.message

    @property
    def handles_closed(self) -> bool:
        return self.handle_cleanup_state == "closed"


def _policy_mismatch() -> VerificationRunnerRuntimeError:
    return VerificationRunnerRuntimeError(
        "policy_mismatch",
        "the installed Runner implementation does not match its release manifest",
        "closed",
    )


def _runtime_unavailable(
    *,
    handle_cleanup_state: RuntimeHandleCleanupState = "closed",
) -> VerificationRunnerRuntimeError:
    return VerificationRunnerRuntimeError(
        "runtime_unavailable",
        "the fixed package runtime could not be verified",
        handle_cleanup_state,
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
    """A resource-free owner bound by the service before native acquisition."""

    __slots__ = (
        "_materialized_root",
        "_scratch_root",
        "_executable",
        "_primary",
        "_probe",
        "_state",
        "_lock",
    )

    def __init__(
        self,
        materialized_root: str | os.PathLike[str],
        scratch_root: str | os.PathLike[str],
    ) -> None:
        self._materialized_root = materialized_root
        self._scratch_root = scratch_root
        self._executable: Path | None = None
        self._primary = _OwnedRuntimeHandle()
        self._probe = _OwnedRuntimeHandle()
        self._state: _RuntimeLeasePhase = "new"
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "RunnerFixedExecutableLease(closed=%r)" % self.closed

    def _slot_locked(self, name: _RuntimeHandleSlotName) -> _OwnedRuntimeHandle:
        if name == "primary":
            return self._primary
        if name == "probe":
            return self._probe
        raise _runtime_unavailable(handle_cleanup_state="uncertain")

    def _acquire_identity_locked(
        self,
        name: _RuntimeHandleSlotName,
        path: Path,
    ) -> _RuntimeFileIdentity:
        slot = self._slot_locked(name)
        allowed = {"empty"} if name == "primary" else {"empty", "closed"}
        if slot.phase not in allowed or slot.handle:
            raise _runtime_unavailable(handle_cleanup_state="uncertain")
        create_file = _kernel32().CreateFileW
        native_path = str(path)
        raw: object = 0
        try:
            slot.handle, slot.close_attempts, slot.phase = 0, 0, "acquiring"
            raw = create_file(
                native_path,
                _GENERIC_READ,
                _FILE_SHARE_READ,
                None,
                _OPEN_EXISTING,
                _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
            handle = _runtime_handle_value(raw)
            if not handle:
                slot.handle, slot.close_attempts, slot.phase = 0, 0, "closed"
                raise _runtime_unavailable()
            slot.handle, slot.close_attempts, slot.phase = handle, 0, "open"
            flags = ctypes.c_uint32()
            if not _kernel32().GetHandleInformation(
                ctypes.c_void_p(handle),
                ctypes.byref(flags),
            ) or flags.value & _HANDLE_FLAG_INHERIT:
                raise _runtime_unavailable()
            return _query_identity(handle)
        except BaseException:
            if slot.phase == "acquiring":
                try:
                    cleanup_handle = _runtime_handle_value(raw)
                except BaseException:
                    slot.handle, slot.close_attempts, slot.phase = 0, 0, "uncertain"
                    raise
                if cleanup_handle:
                    slot.handle, slot.close_attempts, slot.phase = (
                        cleanup_handle,
                        0,
                        "open",
                    )
                else:
                    slot.handle, slot.close_attempts, slot.phase = 0, 0, "uncertain"
            raise

    def _release_slot_locked(
        self,
        slot: _OwnedRuntimeHandle,
    ) -> RuntimeHandleCleanupState:
        if slot.phase in {"empty", "closed"} and not slot.handle:
            return "closed"
        if slot.phase == "acquiring" and slot.handle:
            slot.phase = "open"
        if slot.phase in {"acquiring", "closing", "uncertain"}:
            slot.handle, slot.phase = 0, "uncertain"
            return "uncertain"
        if slot.phase != "open" or not slot.handle:
            slot.handle, slot.phase = 0, "uncertain"
            return "uncertain"
        if slot.close_attempts >= 2:
            return "open"
        slot.close_attempts += 1
        slot.phase = "closing"
        cleanup_state = _close_runtime_handle_once(slot.handle)
        if cleanup_state == "closed":
            slot.handle, slot.phase = 0, "closed"
            return "closed"
        if cleanup_state == "uncertain":
            slot.handle, slot.phase = 0, "uncertain"
            return "uncertain"
        slot.phase = "open"
        return "open"

    def _release_probe_locked(self) -> RuntimeHandleCleanupState:
        return self._release_slot_locked(self._probe)

    def _aggregate_cleanup_state_locked(self) -> RuntimeHandleCleanupState:
        aggregate: RuntimeHandleCleanupState = "closed"
        for slot in (self._primary, self._probe):
            if slot.phase in {"acquiring", "closing", "uncertain"}:
                aggregate = _merge_handle_cleanup_state(aggregate, "uncertain")
            elif slot.phase == "open" and slot.handle:
                aggregate = _merge_handle_cleanup_state(aggregate, "open")
            elif slot.phase not in {"empty", "closed"} or slot.handle:
                aggregate = _merge_handle_cleanup_state(aggregate, "uncertain")
        return aggregate

    @property
    def executable(self) -> Path:
        with self._lock:
            if (
                self._state != "active"
                or self._primary.phase != "open"
                or not self._primary.handle
                or self._executable is None
            ):
                raise _runtime_unavailable(handle_cleanup_state="uncertain")
            return self._executable

    @property
    def closed(self) -> bool:
        with self._lock:
            return bool(
                self._state == "released"
                and self._aggregate_cleanup_state_locked() == "closed"
            )

    def close(self) -> None:
        with self._lock:
            if (
                self._state == "released"
                and self._aggregate_cleanup_state_locked() == "closed"
            ):
                return
            if self._state in {"active", "exiting"}:
                raise _runtime_unavailable(
                    handle_cleanup_state=self._aggregate_cleanup_state_locked()
                )
        cleanup_state = self.finalize_owner()
        if cleanup_state != "closed":
            raise _runtime_unavailable(handle_cleanup_state=cleanup_state)

    def finalize_owner(self) -> RuntimeHandleCleanupState:
        """Idempotently settle both fixed slots within their native retry budgets."""

        with self._lock:
            self._state = "released"
            self._release_slot_locked(self._probe)
            self._release_slot_locked(self._primary)
            return self._aggregate_cleanup_state_locked()

    def __enter__(self) -> Path:
        acquisition_started = False
        try:
            with self._lock:
                if self._state != "new":
                    raise _runtime_unavailable(handle_cleanup_state="uncertain")
                acquisition_started = True
                self._state = "entering"
                executable = _bind_fixed_package_runtime(self)
                if self._primary.phase != "open" or not self._primary.handle:
                    raise _runtime_unavailable(handle_cleanup_state="uncertain")
                self._executable = executable
                self._state = "active"
            return executable
        except BaseException as exc:
            if not acquisition_started:
                raise
            cleanup_state = self.finalize_owner()
            if cleanup_state != "closed":
                raise _runtime_unavailable(
                    handle_cleanup_state=cleanup_state
                ) from exc
            raise

    def __exit__(self, *_args: object) -> None:
        with self._lock:
            if self._state != "active":
                raise _runtime_unavailable(handle_cleanup_state="uncertain")
            self._state = "exiting"
        cleanup_state = self.finalize_owner()
        body_failed = bool(_args and _args[0] is not None)
        if cleanup_state != "closed" and not body_failed:
            raise _runtime_unavailable(handle_cleanup_state=cleanup_state)


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


def _bind_parent_process_executable(
    owner: RunnerFixedExecutableLease,
) -> tuple[Path, _RuntimeFileIdentity]:
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
        observed_identity = owner._acquire_identity_locked("primary", observed)
        declared_identity = owner._acquire_identity_locked("probe", declared)
        _observe_physical_path(observed, directory=False)
        _observe_physical_path(declared, directory=False)
        if observed_identity != declared_identity:
            raise _runtime_unavailable()
        declared_close = owner._release_probe_locked()
        if declared_close != "closed":
            raise _runtime_unavailable(handle_cleanup_state=declared_close)
        return observed, observed_identity
    except VerificationRunnerRuntimeError:
        raise
    except (AttributeError, OSError, RuntimeError, UnicodeError) as exc:
        raise _runtime_unavailable() from exc


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


def _close_runtime_handle_once(handle: int) -> RuntimeHandleCleanupState:
    if not handle:
        return "closed"
    try:
        close_handle = _kernel32().CloseHandle
        native_handle = ctypes.c_void_p(handle)
    except BaseException:
        return "open"
    try:
        return "closed" if close_handle(native_handle) else "open"
    except BaseException:
        return "uncertain"


def _corroborate_locked_path(
    owner: RunnerFixedExecutableLease,
    path: Path,
    expected: _RuntimeFileIdentity,
) -> bool:
    try:
        observed = owner._acquire_identity_locked("probe", path)
        close_state = owner._release_probe_locked()
        if close_state != "closed":
            raise _runtime_unavailable(handle_cleanup_state=close_state)
        return observed == expected
    except VerificationRunnerRuntimeError:
        raise
    except (AttributeError, OSError, RuntimeError):
        return False


def _bind_fixed_package_runtime(
    owner: RunnerFixedExecutableLease,
) -> Path:
    """Bind the fixed runtime into a service-prebound owner."""

    try:
        target = _bounded_absolute_path(os.fspath(owner._materialized_root))
        scratch = _bounded_absolute_path(os.fspath(owner._scratch_root))
        if (
            target.name != "target"
            or scratch.name != "scratch"
            or _path_key(target.parent) != _path_key(scratch.parent)
            or _path_key(target) == _path_key(scratch)
        ):
            raise _runtime_unavailable()
        _observe_physical_path(target, directory=True)
        _observe_physical_path(scratch, directory=True)
        executable, identity = _bind_parent_process_executable(owner)
        if executable.name.casefold() != "python.exe":
            raise _runtime_unavailable()
        _observe_physical_path(executable, directory=False)
        if _is_beneath(executable, target) or _is_beneath(executable, scratch):
            raise _runtime_unavailable()
        if not _corroborate_locked_path(owner, executable, identity):
            raise _runtime_unavailable()
        return executable
    except VerificationRunnerRuntimeError:
        raise
    except (OSError, RuntimeError, TypeError, UnicodeError) as exc:
        raise _runtime_unavailable() from exc


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
]
