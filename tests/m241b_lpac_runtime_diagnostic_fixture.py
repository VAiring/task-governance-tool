"""Private TG-M24.1B current-runtime LPAC diagnostic fixture.

This module is test-only.  It deliberately does not import the inactive
TG-M24.2 process provider.  The native path mirrors the running base CPython,
seals that disposable mirror for one exact AppContainer SID, creates an LPAC
child suspended through the accepted TG-M24.1A seam, and gives a diskless
collector a bounded observation window around the sole resume.

Only digest-bound logical object identifiers and the closed P1 evidence model
may escape.  Child output, process identifiers, paths, provider payloads,
native status values, and collector logs are never fields of the result.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import ntpath
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from task_governance_tool import _verification_runner_lpac_win32 as lpac
from tests import m241a_lpac_native_fixture as portability
from tests import m241b_runtime_qualification_support as evidence
from tests import m241b_runtime_trace_win32 as runtime_trace


RUNTIME_DIGEST_DOMAIN = b"taskgov-verification-runtime-v1\0"
OBJECT_REF_DOMAIN = b"taskgov-m241b-runtime-object-v1\0"
TREE_PROOF_DOMAIN = b"taskgov-m241b-runtime-dacl-v1\0"
RUNTIME_ENTRY_LIMIT = 20_000
RUNTIME_TRAVERSAL_LIMIT = 20_000
RUNTIME_FILE_LIMIT = 32 * 1024 * 1024
RUNTIME_TOTAL_LIMIT = 512 * 1024 * 1024
RUNTIME_INVENTORY_LIMIT = 16 * 1024 * 1024
RUNTIME_COPY_CHUNK = 128 * 1024
RUNTIME_TIMEOUT_SECONDS = 30.0
TREE_ENTRY_LIMIT = 100_000
TREE_DESCRIPTOR_LIMIT = 64 * 1024 * 1024
TREE_TIMEOUT_SECONDS = 30.0
CHILD_WAIT_MILLISECONDS = 15_000
STATUS_ACCESS_DENIED = 0xC0000022
FIXED_DIAGNOSTIC_BOOTSTRAP = "raise SystemExit(0)\n"

_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_EXCLUDED_DIRECTORIES = frozenset(
    {
        "__pycache__",
        "site-packages",
        "ensurepip",
        "pip",
        "setuptools",
        "wheel",
        "pkg_resources",
        "_distutils_hack",
    }
)
_EXCLUDED_SUFFIXES = frozenset({".pth", ".pyc", ".pyo"})
_EXCLUDED_AUTOSTART = frozenset({"sitecustomize.py", "usercustomize.py"})
_TOP_LEVEL_RUNTIME_NAMES = frozenset(
    {"python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"}
)
_SYSTEM_IMAGE_CANDIDATES = (
    "advapi32.dll",
    "bcrypt.dll",
    "bcryptprimitives.dll",
    "cfgmgr32.dll",
    "combase.dll",
    "crypt32.dll",
    "gdi32.dll",
    "gdi32full.dll",
    "imm32.dll",
    "kernel32.dll",
    "kernelbase.dll",
    "msvcp_win.dll",
    "ntdll.dll",
    "ole32.dll",
    "oleaut32.dll",
    "powrprof.dll",
    "profapi.dll",
    "rpcrt4.dll",
    "sechost.dll",
    "shell32.dll",
    "shlwapi.dll",
    "ucrtbase.dll",
    "user32.dll",
    "userenv.dll",
    "version.dll",
    "win32u.dll",
    "ws2_32.dll",
)
_KNOWN_DLL_VALUE_LIMIT = 256
_KNOWN_DLL_TEXT_LIMIT = 32_768

_SAFE_ERROR_CODES = frozenset(
    {
        "diagnostic_unavailable",
        "diagnostic_boundary_violation",
        "diagnostic_collector_failed",
        "diagnostic_baseline_mismatch",
        "diagnostic_cleanup_failed",
    }
)


class RuntimeDiagnosticError(RuntimeError):
    """Fixed non-retaining failure from the private diagnostic fixture."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _SAFE_ERROR_CODES else "diagnostic_boundary_violation"
        super().__init__("TG-M24.1B runtime diagnostic failed closed")


def _fail(code: str) -> None:
    raise RuntimeDiagnosticError(code)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        _fail("diagnostic_boundary_violation")


def _domain_digest(domain: bytes, value: object) -> str:
    digest = hashlib.sha256()
    digest.update(domain)
    digest.update(_canonical_bytes(value))
    return "sha256:" + digest.hexdigest()


def build_diagnostic_argv(runtime_executable: Path) -> tuple[str, ...]:
    """Build exact M24 interpreter flags with one inline fixture-owned body."""

    if (
        not runtime_executable.is_absolute()
        or runtime_executable.name.casefold() != "python.exe"
    ):
        _fail("diagnostic_boundary_violation")
    return (
        str(runtime_executable),
        "-I",
        "-B",
        "-X",
        "utf8",
        "-c",
        FIXED_DIAGNOSTIC_BOOTSTRAP,
    )


@dataclass(frozen=True, slots=True)
class RuntimeEntry:
    path: str
    size_bytes: int
    sha256: str
    source_path: Path = field(repr=False, compare=False)

    def canonical_value(self) -> dict[str, object]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class RuntimeCapture:
    version: tuple[int, int, int]
    entries: tuple[RuntimeEntry, ...]
    runtime_digest: str
    canonical_size: int
    total_bytes: int
    source_root: Path = field(repr=False, compare=False)

    def canonical_value(self) -> dict[str, object]:
        return {
            "format_version": 1,
            "implementation": "cpython",
            "version": list(self.version),
            "architecture": "AMD64",
            "entries": [item.canonical_value() for item in self.entries],
        }


@dataclass(frozen=True, slots=True)
class RuntimeMirror:
    entries: tuple[RuntimeEntry, ...]
    runtime_digest: str
    canonical_size: int
    total_bytes: int
    root: Path = field(repr=False, compare=False)
    executable: Path = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class CollectorObject:
    plane: str
    object_ref: str
    match_kind: str
    component: str = field(repr=False)
    dependency_origin: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class CollectorInventory:
    runtime_digest: str
    manifest: evidence.InventoryManifest
    objects: tuple[CollectorObject, ...] = field(repr=False)

    def resolve(
        self, *, plane: str, match_kind: str, component: str
    ) -> str | None:
        """Resolve only a prebound exact identity; unknown never becomes a bucket."""

        matches = tuple(
            item.object_ref
            for item in self.objects
            if item.plane == plane
            and item.match_kind == match_kind
            and item.component == component
        )
        if len(matches) > 1:
            _fail("diagnostic_boundary_violation")
        return matches[0] if matches else None


@dataclass(frozen=True, slots=True)
class CollectorClassification:
    document: bytes = field(repr=False)
    quality: evidence.CollectionQualityProof
    cleanup_proved: bool


@dataclass(frozen=True, slots=True)
class TreeDaclProof:
    entry_count: int
    descriptor_bytes: int
    proof_digest: str


@dataclass(frozen=True, slots=True)
class DiagnosticCleanupProof:
    child_job_zero: bool
    control_job_zero: bool
    all_handles_closed: bool
    profile_absent: bool
    temporary_absent: bool


@dataclass(frozen=True, slots=True)
class NativeDiagnosticResult:
    runtime_digest: str
    inventory_manifest_digest: str
    dacl_proof_digest: str
    dacl_entry_count: int
    route: str
    normal_control_created: bool
    resume_count: int
    access_denied_baseline: bool
    root_cause: evidence.RootCauseEvidence
    cleanup: DiagnosticCleanupProof


class DiagnosticCollector(Protocol):
    """Adapter implemented by the separate diskless four-plane collector."""

    def start_for_suspended_child(
        self,
        *,
        application: Path,
        inventory: CollectorInventory,
        process_id: int,
        process_handle: lpac.OwnedHandle,
    ) -> None: ...

    def stop_and_classify(self, *, access_denied: bool) -> CollectorClassification: ...

    def abort(self) -> None: ...


def _path_identity(value: str) -> str | None:
    if (
        type(value) is not str
        or not value
        or len(value) > 32_768
        or "\0" in value
    ):
        return None
    normalized = value.replace("/", "\\")
    if normalized.casefold().startswith("\\\\?\\unc\\"):
        return None
    for prefix in ("\\\\?\\", "\\??\\"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if not re.match(r"^[A-Za-z]:\\", normalized):
        return None
    if any(part in {"", ".", ".."} for part in normalized[3:].split("\\")):
        return None
    return ntpath.normpath(normalized)


def _nt_path_identity(value: str) -> str | None:
    if (
        type(value) is not str
        or not value
        or len(value) > 32_768
        or "\0" in value
        or not value.casefold().startswith("\\device\\")
    ):
        return None
    raw = value.replace("/", "\\")
    parts = raw.split("\\")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        return None
    normalized = ntpath.normpath(raw)
    return normalized if normalized.casefold().startswith("\\device\\") else None


def _physical_path_identity(value: str) -> str | None:
    dos = _path_identity(value)
    if dos is not None:
        return "dos:" + dos
    device = _nt_path_identity(value)
    return "nt:" + device if device is not None else None


def _logical_dependency_identity(value: str) -> str | None:
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or "\0" in value
        or "/" in value
        or "\\" in value
        or ":" in value
        or _COMPONENT.fullmatch(value) is None
        or not value.casefold().endswith(".dll")
    ):
        return None
    return value.casefold()


def _logical_dependency_component(value: str) -> tuple[str, str] | None:
    if not value.startswith("logical-dependency:"):
        return None
    parts = value.split(":", 2)
    if len(parts) != 3 or parts[1] not in {"runtime", "known_dll"}:
        return None
    name = _logical_dependency_identity(parts[2])
    if name is None or name != parts[2]:
        return None
    return parts[1], name


def _registry_identity(value: str) -> str | None:
    if (
        type(value) is not str
        or not value
        or len(value) > 32_768
        or "\0" in value
    ):
        return None
    normalized = ntpath.normpath(value.replace("/", "\\")).strip("\\")
    return normalized.casefold() if normalized else None


class _AliasLease:
    """Sealed test-private ownership protocol for one trusted held file."""

    aliases: tuple[str, str]
    file_identity: tuple[int, bytes]

    def reprove(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _ExactInventoryResolver:
    """Transient exact raw-to-opaque resolver; unknown identities stay unknown."""

    def __init__(self) -> None:
        # Ownership exists before any native alias handle can be acquired.
        # Callers must publish this object to their cleanup owner, then invoke
        # initialize(); an initialization failure may retain a close-failed
        # lease for a later bounded abort retry.
        self._paths: dict[str, dict[str, str]] = {
            plane: {} for plane in evidence.PLANE_ORDER
        }
        self._registry: dict[str, str] = {}
        self._loader_dependencies: dict[str, str] = {}
        self._leases: list[_AliasLease] = []
        self._closed = False
        self._initialized = False
        self._ready = False
        self._stable_proved = False
        self._ordinal_equal: Callable[[str, str], bool] | None = None

    def initialize(
        self,
        *,
        application: Path,
        inventory: CollectorInventory,
        system32_root: Path,
        current_user_sid: str,
        alias_lease_factory: Callable[..., _AliasLease],
        ordinal_equal: Callable[[str, str], bool],
    ) -> None:
        if (
            self._initialized
            or self._closed
            or type(inventory) is not CollectorInventory
            or not application.is_absolute()
            or application.name.casefold() != "python.exe"
            or not system32_root.is_absolute()
            or type(current_user_sid) is not str
            or not current_user_sid.startswith("S-1-")
            or not callable(alias_lease_factory)
            or not callable(ordinal_equal)
        ):
            _fail("diagnostic_collector_failed")
        self._initialized = True
        self._ordinal_equal = ordinal_equal
        runtime_root = application.parent
        attempt_root = runtime_root.parent
        physical: dict[
            tuple[str, bool],
            list[tuple[str, str, str | None, str | None]],
        ] = {}
        dependencies: list[CollectorObject] = []
        try:
            for item in inventory.objects:
                component = item.component
                path: Path | None = None
                is_directory = False
                image_name: str | None = None
                image_origin: str | None = None
                if component == "attempt-root":
                    path = attempt_root
                    is_directory = True
                elif component.startswith("attempt-relative:"):
                    relative = component.removeprefix("attempt-relative:")
                    path = attempt_root.joinpath(*relative.split("/"))
                    is_directory = True
                elif component.startswith("runtime-relative:"):
                    relative = component.removeprefix("runtime-relative:")
                    path = runtime_root.joinpath(*relative.split("/"))
                elif component.startswith("system32-basename:"):
                    name = component.removeprefix("system32-basename:")
                    path = system32_root / name
                elif component.startswith("runtime-image:"):
                    relative = component.removeprefix("runtime-image:")
                    path = runtime_root.joinpath(*relative.split("/"))
                    image_name = _logical_dependency_identity(
                        ntpath.basename(relative)
                    )
                    image_origin = "runtime"
                elif component.startswith("system32-image:"):
                    name = component.removeprefix("system32-image:")
                    path = system32_root / name
                    image_name = _logical_dependency_identity(name)
                    image_origin = "known_dll"
                elif component.startswith("logical-dependency:"):
                    dependency = _logical_dependency_component(component)
                    if (
                        dependency is None
                        or item.dependency_origin != dependency[0]
                    ):
                        _fail("diagnostic_collector_failed")
                    dependencies.append(item)
                    continue
                elif component.startswith("registry-key:"):
                    if item.dependency_origin is not None:
                        _fail("diagnostic_collector_failed")
                    self._add_registry_component(
                        component.removeprefix("registry-key:"),
                        item.object_ref,
                        current_user_sid=current_user_sid,
                    )
                    continue
                else:
                    _fail("diagnostic_collector_failed")
                if (
                    item.dependency_origin is not None
                    or path is None
                    or not path.is_absolute()
                ):
                    _fail("diagnostic_collector_failed")
                key = (ntpath.normpath(str(path)), is_directory)
                physical.setdefault(key, []).append(
                    (item.plane, item.object_ref, image_name, image_origin)
                )

            images_by_name: dict[
                str,
                dict[str, dict[tuple[int, bytes], _AliasLease]],
            ] = {}
            identities_by_plane: dict[
                tuple[str, tuple[int, bytes]], str
            ] = {}
            for (raw_path, is_directory), bindings in sorted(
                physical.items(), key=lambda item: item[0]
            ):
                lease = alias_lease_factory(
                    Path(raw_path), is_directory=is_directory
                )
                if not isinstance(lease, _AliasLease):
                    _fail("diagnostic_collector_failed")
                self._leases.append(lease)
                if (
                    type(lease.aliases) is not tuple
                    or len(lease.aliases) != 2
                    or type(lease.file_identity) is not tuple
                    or len(lease.file_identity) != 2
                    or type(lease.file_identity[0]) is not int
                    or lease.file_identity[0] <= 0
                    or type(lease.file_identity[1]) is not bytes
                    or len(lease.file_identity[1]) != 16
                    or not callable(lease.close)
                ):
                    _fail("diagnostic_collector_failed")
                alias_keys = tuple(
                    _physical_path_identity(value) for value in lease.aliases
                )
                if (
                    None in alias_keys
                    or len(set(alias_keys)) != 2
                    or not any(key.startswith("dos:") for key in alias_keys)
                    or not any(key.startswith("nt:") for key in alias_keys)
                ):
                    _fail("diagnostic_collector_failed")
                for plane, object_ref, image_name, image_origin in bindings:
                    identity_key = (plane, lease.file_identity)
                    existing_identity = identities_by_plane.get(identity_key)
                    if (
                        existing_identity is not None
                        and existing_identity != object_ref
                    ):
                        _fail("diagnostic_collector_failed")
                    identities_by_plane[identity_key] = object_ref
                    for alias_key in alias_keys:
                        assert alias_key is not None
                        self._insert_path(plane, alias_key, object_ref)
                    if image_name is not None:
                        assert image_origin is not None
                        images_by_name.setdefault(image_name, {}).setdefault(
                            image_origin, {}
                        )[lease.file_identity] = lease

            for item in dependencies:
                dependency = _logical_dependency_component(item.component)
                name = dependency[1] if dependency is not None else None
                if (
                    item.plane != "dll_image_load"
                    or name is None
                    or name in self._loader_dependencies
                    or item.match_kind != "exact_loader_dependency"
                ):
                    _fail("diagnostic_collector_failed")
                observed_origins = images_by_name.get(name, {})
                if item.dependency_origin in {"runtime", "known_dll"}:
                    if (
                        set(observed_origins) != {item.dependency_origin}
                        or len(observed_origins[item.dependency_origin]) != 1
                    ):
                        _fail("diagnostic_collector_failed")
                else:
                    _fail("diagnostic_collector_failed")
                self._loader_dependencies[name] = item.object_ref
            self._ready = True
        except BaseException:
            cleanup_failed = False
            try:
                self.close()
            except BaseException:
                cleanup_failed = True
            if cleanup_failed:
                raise RuntimeDiagnosticError("diagnostic_cleanup_failed") from None
            raise RuntimeDiagnosticError("diagnostic_collector_failed") from None

    @property
    def ready(self) -> bool:
        return self._ready and not self._closed

    def _add_registry_component(
        self,
        component: str,
        object_ref: str,
        *,
        current_user_sid: str,
    ) -> None:
        aliases: tuple[str, ...]
        if component.startswith("HKLM/"):
            tail = component.removeprefix("HKLM/").replace("/", "\\")
            aliases = (
                "HKLM\\" + tail,
                "HKEY_LOCAL_MACHINE\\" + tail,
                "\\REGISTRY\\MACHINE\\" + tail,
            )
        elif component.startswith("HKCU/"):
            tail = component.removeprefix("HKCU/").replace("/", "\\")
            aliases = (
                "HKCU\\" + tail,
                "HKEY_CURRENT_USER\\" + tail,
                "\\REGISTRY\\USER\\" + current_user_sid + "\\" + tail,
            )
        elif component.startswith("APPCONTAINER/") and component.endswith(
            "/ProfileRoot"
        ):
            package_sid = component.split("/", 2)[1]
            aliases = (
                "APPCONTAINER\\" + package_sid + "\\ProfileRoot",
                "\\REGISTRY\\USER\\"
                + current_user_sid
                + "\\Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows"
                "\\CurrentVersion\\AppContainer\\Storage\\"
                + package_sid,
            )
        else:
            _fail("diagnostic_collector_failed")
        for alias in aliases:
            key = _registry_identity(alias)
            if key is None:
                _fail("diagnostic_collector_failed")
            existing = self._registry.get(key)
            if existing is not None and existing != object_ref:
                _fail("diagnostic_collector_failed")
            self._registry[key] = object_ref

    def _insert_path(self, plane: str, key: str, object_ref: str) -> None:
        if self._ordinal_equal is None:
            _fail("diagnostic_collector_failed")
        for existing_key, existing_ref in self._paths[plane].items():
            if self._ordinal_equal(existing_key, key):
                if existing_ref != object_ref:
                    _fail("diagnostic_collector_failed")
                return
        self._paths[plane][key] = object_ref

    def _lookup_path(self, plane: str, key: str) -> str | None:
        if self._ordinal_equal is None:
            return None
        matches = tuple(
            object_ref
            for candidate, object_ref in self._paths.get(plane, {}).items()
            if self._ordinal_equal(candidate, key)
        )
        if len(set(matches)) > 1:
            return None
        return matches[0] if matches else None

    def __call__(self, plane: str, raw_identity: str) -> str | None:
        if not self.ready:
            return None
        if plane == "registry_access":
            key = _registry_identity(raw_identity)
            return self._registry.get(key) if key is not None else None
        key = _physical_path_identity(raw_identity)
        return self._lookup_path(plane, key) if key is not None else None

    def resolve_loader_dependency(self, raw_identity: str) -> str | None:
        if not self.ready:
            return None
        key = _logical_dependency_identity(raw_identity)
        return self._loader_dependencies.get(key) if key is not None else None

    def prove_stable(self) -> None:
        if not self.ready or self._stable_proved:
            _fail("diagnostic_collector_failed")
        failed = False
        for lease in self._leases:
            try:
                lease.reprove()
            except BaseException:
                failed = True
        if failed:
            _fail("diagnostic_collector_failed")
        self._stable_proved = True

    def close(self) -> None:
        if self._closed and not self._leases:
            return
        for values in self._paths.values():
            values.clear()
        self._registry.clear()
        self._loader_dependencies.clear()
        cleanup_failed = False
        retained: list[_AliasLease] = []
        for lease in reversed(self._leases):
            try:
                lease.close()
            except BaseException:
                cleanup_failed = True
                retained.append(lease)
        self._leases = list(reversed(retained))
        self._closed = not self._leases
        self._ready = False
        if cleanup_failed:
            _fail("diagnostic_cleanup_failed")


class _RealtimeCollectorAdapter:
    """Connect the fixture lifecycle to the separate ETW capability owner."""

    def __init__(
        self,
        *,
        collector_factory: object = runtime_trace.RealtimeRuntimeCollector,
        system32_root: Path | None = None,
        current_user_sid: str | None = None,
        alias_lease_factory: Callable[..., _AliasLease] | None = None,
        ordinal_equal: Callable[[str, str], bool] | None = None,
    ) -> None:
        if not callable(collector_factory):
            _fail("diagnostic_collector_failed")
        self._factory = collector_factory
        self._system32_root = system32_root
        self._current_user_sid = current_user_sid
        self._alias_lease_factory = alias_lease_factory
        self._ordinal_equal = ordinal_equal
        self._collector: object | None = None
        self._resolver: _ExactInventoryResolver | None = None
        self._inventory: CollectorInventory | None = None
        self._terminal = False

    def start_for_suspended_child(
        self,
        *,
        application: Path,
        inventory: CollectorInventory,
        process_id: int,
        process_handle: lpac.OwnedHandle,
    ) -> None:
        if (
            self._terminal
            or self._collector is not None
            or self._resolver is not None
            or type(inventory) is not CollectorInventory
            or type(process_id) is not int
            or process_id <= 0
            or not isinstance(process_handle, lpac.OwnedHandle)
            or process_handle.closed
        ):
            _fail("diagnostic_collector_failed")
        system32_root = self._system32_root or _system32_root()
        current_user_sid = self._current_user_sid or lpac._current_user_sid()
        alias_lease_factory = (
            self._alias_lease_factory or _open_trusted_alias_lease
        )
        ordinal_equal = self._ordinal_equal or _windows_ordinal_equal
        resolver = _ExactInventoryResolver()
        self._resolver = resolver
        resolver.initialize(
            application=application,
            inventory=inventory,
            system32_root=system32_root,
            current_user_sid=current_user_sid,
            alias_lease_factory=alias_lease_factory,
            ordinal_equal=ordinal_equal,
        )
        try:
            if not resolver.ready:
                _fail("diagnostic_collector_failed")
            objects_by_plane = {
                plane.plane: plane.object_refs
                for plane in inventory.manifest.planes
            }
            binding = runtime_trace.InventoryBinding(
                runtime_digest=inventory.runtime_digest,
                objects_by_plane=objects_by_plane,
                loader_dependency_refs=tuple(
                    item.object_ref
                    for item in inventory.objects
                    if item.plane == "dll_image_load"
                    and item.match_kind == "exact_loader_dependency"
                ),
                path_resolver=resolver,
                loader_dependency_resolver=resolver.resolve_loader_dependency,
            )
            initial_image_ref = inventory.resolve(
                plane="dll_image_load",
                match_kind="exact_image_identity",
                component="runtime-image:python.exe",
            )
            if initial_image_ref is None:
                _fail("diagnostic_collector_failed")
            collector = self._factory(binding)
            self._collector = collector
            self._inventory = inventory
            collector.start_for_suspended_child(
                process_id=process_id,
                process_handle=process_handle.value,
                initial_image_object_ref=initial_image_ref,
            )
        except BaseException as error:
            cleanup_failed = (
                isinstance(error, runtime_trace.RuntimeTraceError)
                and error.code == "trace_cleanup_unproved"
            )
            if self._collector is not None:
                try:
                    self._collector.abort()
                    self._collector = None
                except BaseException:
                    cleanup_failed = True
            try:
                resolver.close()
                self._resolver = None
            except BaseException:
                cleanup_failed = True
            self._inventory = None
            self._terminal = (
                self._collector is None and self._resolver is None
            )
            if cleanup_failed:
                raise RuntimeDiagnosticError("diagnostic_cleanup_failed") from None
            raise RuntimeDiagnosticError("diagnostic_collector_failed") from None

    def stop_and_classify(self, *, access_denied: bool) -> CollectorClassification:
        if (
            access_denied is not True
            or self._terminal
            or self._collector is None
            or self._inventory is None
            or self._resolver is None
            or not self._resolver.ready
        ):
            _fail("diagnostic_collector_failed")
        collector = self._collector
        inventory = self._inventory
        resolver = self._resolver
        cleanup_unproved = False
        try:
            collector.record_subject_proof(runtime_trace.SUBJECT_ACCESS_DENIED)
            result = collector.stop()
            if (
                type(result) is runtime_trace.RuntimeTraceResult
                and result.cleanup_proved is not True
            ):
                cleanup_unproved = True
            if (
                type(result) is not runtime_trace.RuntimeTraceResult
                or result.candidate_id != evidence.CURRENT_CANDIDATE_ID
                or result.runtime_digest != inventory.runtime_digest
                or result.subject_proof != runtime_trace.SUBJECT_ACCESS_DENIED
                or type(result.cleanup_proved) is not bool
                or not result.cleanup_proved
                or tuple(item.plane for item in result.quality)
                != evidence.PLANE_ORDER
                or tuple(item.plane for item in result.planes)
                != evidence.PLANE_ORDER
            ):
                _fail("diagnostic_collector_failed")
            # The held leaf handles authorize the transient DOS/NT aliases only
            # for this collection window.  Reprove every leaf after the
            # consumer has joined and before any classification is materialized.
            resolver.prove_stable()
            quality = evidence.bind_collection_quality(
                subject_proof=evidence.STOCK_CHILD_ACCESS_DENIED_PROOF,
                window_binding=result.window_binding,
                inventory_manifest=inventory.manifest,
                planes=tuple(
                    evidence.PlaneCollectionQualityInput(
                        plane=item.plane,
                        collection_schema=item.collection_schema,
                        probe_available=item.probe_available,
                        lossless=item.lossless,
                        overflowed=item.overflowed,
                        plane_scope_complete=item.plane_scope_complete,
                        correlation_complete=item.correlation_complete,
                        cleanup_proved=item.cleanup_proved,
                    )
                    for item in result.quality
                ),
            )
            planes: list[dict[str, object]] = []
            for item, quality_item in zip(
                result.planes, quality.planes, strict=True
            ):
                expected_reason = quality_item.failure_reason
                if item.outcome == "inconclusive":
                    mapped_reason = (
                        "plane_scope_unproved"
                        if item.reason == "binding_unproved"
                        else item.reason
                    )
                    if mapped_reason != expected_reason:
                        _fail("diagnostic_collector_failed")
                    reason = expected_reason
                else:
                    if expected_reason is not None or item.reason is not None:
                        _fail("diagnostic_collector_failed")
                    reason = None
                planes.append(
                    {
                        "object_ref": item.object_ref,
                        "operation": item.operation,
                        "outcome": item.outcome,
                        "plane": item.plane,
                        "policy": item.policy,
                        "reason": reason,
                    }
                )
            document = _canonical_bytes(
                {
                    "candidate_id": evidence.CURRENT_CANDIDATE_ID,
                    "collection_proof_digest": quality.proof_digest,
                    "exit_binding": evidence.STOCK_CHILD_ACCESS_DENIED_PROOF.exit_binding,
                    "inventory_manifest_digest": inventory.manifest.manifest_digest,
                    "planes": planes,
                    "runtime_digest": inventory.runtime_digest,
                    "schema_version": evidence.SCHEMA_VERSION,
                    "subject": evidence.STOCK_CHILD_ACCESS_DENIED_PROOF.subject,
                }
            )
            classification = CollectorClassification(document, quality, True)
            if self._resolver is not None:
                try:
                    self._resolver.close()
                    self._resolver = None
                except BaseException:
                    cleanup_unproved = True
                    raise
        except BaseException as error:
            cleanup_failed = cleanup_unproved or (
                isinstance(error, runtime_trace.RuntimeTraceError)
                and error.code == "trace_cleanup_unproved"
            )
            try:
                # Until the complete result validates, the adapter still owns
                # a possibly live session.  The concrete collector makes this
                # idempotent after a clean stop and fail-closed otherwise.
                collector.abort()
                self._collector = None
            except BaseException:
                cleanup_failed = True
            self._inventory = None
            if self._resolver is not None:
                try:
                    self._resolver.close()
                    self._resolver = None
                except BaseException:
                    cleanup_failed = True
            self._terminal = (
                self._collector is None and self._resolver is None
            )
            if cleanup_failed:
                raise RuntimeDiagnosticError("diagnostic_cleanup_failed") from None
            raise RuntimeDiagnosticError("diagnostic_collector_failed") from None
        self._terminal = True
        self._collector = None
        self._inventory = None
        return classification

    def abort(self) -> None:
        if (
            self._terminal
            and self._collector is None
            and self._resolver is None
        ):
            return
        cleanup_failed = False
        if self._collector is not None:
            try:
                self._collector.abort()
                self._collector = None
            except BaseException:
                cleanup_failed = True
        if self._resolver is not None:
            try:
                self._resolver.close()
                self._resolver = None
            except BaseException:
                cleanup_failed = True
        self._terminal = (
            self._collector is None and self._resolver is None
        )
        self._inventory = None
        if cleanup_failed:
            raise RuntimeDiagnosticError("diagnostic_cleanup_failed") from None


def _validate_relative_path(value: str) -> str:
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeError:
        _fail("diagnostic_unavailable")
    parts = value.split("/")
    if (
        not encoded
        or len(parts) > 32
        or any(
            part in {"", ".", ".."} or _COMPONENT.fullmatch(part) is None
            for part in parts
        )
    ):
        _fail("diagnostic_unavailable")
    return value


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        junction = getattr(path, "is_junction", None)
        if junction is not None and junction():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except (OSError, RuntimeError):
        _fail("diagnostic_unavailable")


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_size,
        first.st_mtime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_size,
        second.st_mtime_ns,
    )


def _same_directory(first: os.stat_result, second: os.stat_result) -> bool:
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _observe_directory(path: Path) -> os.stat_result:
    try:
        observed = path.lstat()
        if _is_reparse(path) or not stat.S_ISDIR(observed.st_mode):
            _fail("diagnostic_unavailable")
        return observed
    except RuntimeDiagnosticError:
        raise
    except (OSError, RuntimeError):
        _fail("diagnostic_unavailable")


def _hash_file(path: Path) -> tuple[int, str]:
    descriptor: int | None = None
    try:
        before = path.lstat()
        if _is_reparse(path) or not stat.S_ISREG(before.st_mode):
            _fail("diagnostic_unavailable")
        if before.st_size < 0 or before.st_size > RUNTIME_FILE_LIMIT:
            _fail("diagnostic_unavailable")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if not _same_file(before, opened):
            _fail("diagnostic_unavailable")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, RUNTIME_COPY_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > RUNTIME_FILE_LIMIT:
                _fail("diagnostic_unavailable")
            digest.update(chunk)
        if not _same_file(opened, os.fstat(descriptor)) or not _same_file(
            before, path.lstat()
        ):
            _fail("diagnostic_unavailable")
        return total, digest.hexdigest()
    except RuntimeDiagnosticError:
        raise
    except (OSError, RuntimeError):
        _fail("diagnostic_unavailable")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                _fail("diagnostic_cleanup_failed")


def _excluded_directory(name: str) -> bool:
    return name.casefold() in _EXCLUDED_DIRECTORIES


def _excluded_file(name: str) -> bool:
    lowered = name.casefold()
    return (
        name.startswith(".")
        or lowered in _EXCLUDED_AUTOSTART
        or lowered.endswith(".exe")
        or any(lowered.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES)
    )


def _scan_source_directory(
    root: Path,
    relative_root: str,
    *,
    deadline: float,
    observed: list[int],
) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    stack = [(root, relative_root, _observe_directory(root))]
    while stack:
        if time.monotonic() > deadline:
            _fail("diagnostic_unavailable")
        directory, relative, identity = stack.pop()
        if not _same_directory(identity, _observe_directory(directory)):
            _fail("diagnostic_unavailable")
        children: list[os.DirEntry[str]] = []
        try:
            with os.scandir(directory) as iterator:
                for child in iterator:
                    observed[0] += 1
                    if observed[0] > RUNTIME_TRAVERSAL_LIMIT:
                        _fail("diagnostic_unavailable")
                    children.append(child)
            children.sort(key=lambda item: item.name.encode("utf-8", "strict"))
        except RuntimeDiagnosticError:
            raise
        except (OSError, RuntimeError, UnicodeError):
            _fail("diagnostic_unavailable")
        directories: list[tuple[Path, str, os.stat_result]] = []
        for child in children:
            path = directory / child.name
            relative_path = _validate_relative_path(f"{relative}/{child.name}")
            try:
                if child.is_symlink() or _is_reparse(path):
                    _fail("diagnostic_unavailable")
                if child.is_dir(follow_symlinks=False):
                    if not _excluded_directory(child.name):
                        directories.append((path, relative_path, path.lstat()))
                elif child.is_file(follow_symlinks=False):
                    if not _excluded_file(child.name):
                        result.append((relative_path, path))
                        if len(result) > RUNTIME_ENTRY_LIMIT:
                            _fail("diagnostic_unavailable")
                else:
                    _fail("diagnostic_unavailable")
            except RuntimeDiagnosticError:
                raise
            except (OSError, RuntimeError):
                _fail("diagnostic_unavailable")
        stack.extend(reversed(directories))
        if not _same_directory(identity, _observe_directory(directory)):
            _fail("diagnostic_unavailable")
    return result


def capture_current_cpython_runtime() -> RuntimeCapture:
    """Capture only the running base AMD64 CPython and standard library."""

    if (
        os.name != "nt"
        or platform.python_implementation() != "CPython"
        or platform.machine().upper() != "AMD64"
        or sys.prefix != sys.base_prefix
    ):
        _fail("diagnostic_unavailable")
    root = Path(sys.base_prefix)
    executable = Path(sys.executable)
    version = (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
    if (
        not root.is_absolute()
        or not executable.is_absolute()
        or executable.name.casefold() != "python.exe"
    ):
        _fail("diagnostic_unavailable")
    root_identity = _observe_directory(root)
    try:
        if executable.parent.resolve(strict=True) != root.resolve(strict=True):
            _fail("diagnostic_unavailable")
    except RuntimeDiagnosticError:
        raise
    except (OSError, RuntimeError):
        _fail("diagnostic_unavailable")
    version_dll = f"python{version[0]}{version[1]}.dll"
    sources: list[tuple[str, Path]] = [("python.exe", executable)]
    for name in sorted(_TOP_LEVEL_RUNTIME_NAMES | {version_dll}):
        candidate = root / name
        if candidate.exists():
            sources.append((_validate_relative_path(name), candidate))
    if not (root / version_dll).is_file():
        _fail("diagnostic_unavailable")
    deadline = time.monotonic() + RUNTIME_TIMEOUT_SECONDS
    observed = [0]
    for name in ("DLLs", "Lib"):
        directory = root / name
        if not directory.is_dir():
            _fail("diagnostic_unavailable")
        sources.extend(
            _scan_source_directory(
                directory,
                name,
                deadline=deadline,
                observed=observed,
            )
        )
    sources.sort(key=lambda item: item[0].encode("utf-8", "strict"))
    if len(sources) > RUNTIME_ENTRY_LIMIT:
        _fail("diagnostic_unavailable")
    entries: list[RuntimeEntry] = []
    folded: set[str] = set()
    total_bytes = 0
    for relative, source in sources:
        if time.monotonic() > deadline or relative.casefold() in folded:
            _fail("diagnostic_unavailable")
        folded.add(relative.casefold())
        size_bytes, digest = _hash_file(source)
        if total_bytes > RUNTIME_TOTAL_LIMIT - size_bytes:
            _fail("diagnostic_unavailable")
        total_bytes += size_bytes
        entries.append(RuntimeEntry(relative, size_bytes, digest, source))
    if not _same_directory(root_identity, _observe_directory(root)):
        _fail("diagnostic_unavailable")
    provisional = RuntimeCapture(version, tuple(entries), "", 0, total_bytes, root)
    canonical = provisional.canonical_value()
    canonical_size = len(_canonical_bytes(canonical))
    if canonical_size > RUNTIME_INVENTORY_LIMIT:
        _fail("diagnostic_unavailable")
    return RuntimeCapture(
        version,
        tuple(entries),
        _domain_digest(RUNTIME_DIGEST_DOMAIN, canonical),
        canonical_size,
        total_bytes,
        root,
    )


def _copy_runtime_entry(entry: RuntimeEntry, destination: Path) -> None:
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        before = entry.source_path.lstat()
        if _is_reparse(entry.source_path) or not stat.S_ISREG(before.st_mode):
            _fail("diagnostic_unavailable")
        source_descriptor = os.open(
            entry.source_path,
            os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o500,
        )
        opened = os.fstat(source_descriptor)
        if not _same_file(before, opened):
            _fail("diagnostic_unavailable")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_descriptor, RUNTIME_COPY_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
            offset = 0
            while offset < len(chunk):
                written = os.write(destination_descriptor, chunk[offset:])
                if written <= 0:
                    _fail("diagnostic_unavailable")
                offset += written
        if total != entry.size_bytes or digest.hexdigest() != entry.sha256:
            _fail("diagnostic_unavailable")
        if not _same_file(opened, os.fstat(source_descriptor)) or not _same_file(
            before, entry.source_path.lstat()
        ):
            _fail("diagnostic_unavailable")
    except RuntimeDiagnosticError:
        raise
    except (OSError, RuntimeError):
        _fail("diagnostic_unavailable")
    finally:
        cleanup_failed = False
        for descriptor in (destination_descriptor, source_descriptor):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    cleanup_failed = True
        if cleanup_failed:
            _fail("diagnostic_cleanup_failed")


def _scan_mirror(root: Path) -> tuple[tuple[str, Path], ...]:
    deadline = time.monotonic() + RUNTIME_TIMEOUT_SECONDS
    result: list[tuple[str, Path]] = []
    stack = [(root, "")]
    observed = 0
    while stack:
        if time.monotonic() > deadline:
            _fail("diagnostic_unavailable")
        directory, relative = stack.pop()
        children: list[os.DirEntry[str]] = []
        try:
            with os.scandir(directory) as iterator:
                children.extend(iterator)
            children.sort(key=lambda item: item.name.encode("utf-8", "strict"))
        except (OSError, RuntimeError, UnicodeError):
            _fail("diagnostic_unavailable")
        directories: list[tuple[Path, str]] = []
        for child in children:
            observed += 1
            if observed > RUNTIME_TRAVERSAL_LIMIT:
                _fail("diagnostic_unavailable")
            path = directory / child.name
            child_relative = child.name if not relative else f"{relative}/{child.name}"
            _validate_relative_path(child_relative)
            try:
                if child.is_symlink() or _is_reparse(path):
                    _fail("diagnostic_unavailable")
                if child.is_dir(follow_symlinks=False):
                    directories.append((path, child_relative))
                elif child.is_file(follow_symlinks=False):
                    result.append((child_relative, path))
                else:
                    _fail("diagnostic_unavailable")
            except RuntimeDiagnosticError:
                raise
            except (OSError, RuntimeError):
                _fail("diagnostic_unavailable")
        stack.extend(reversed(directories))
    result.sort(key=lambda item: item[0].encode("utf-8", "strict"))
    return tuple(result)


def prove_runtime_mirror(capture: RuntimeCapture, mirror: RuntimeMirror) -> None:
    if (
        type(capture) is not RuntimeCapture
        or type(mirror) is not RuntimeMirror
        or mirror.runtime_digest != capture.runtime_digest
        or mirror.canonical_size != capture.canonical_size
        or mirror.total_bytes != capture.total_bytes
        or mirror.executable != mirror.root / "python.exe"
    ):
        _fail("diagnostic_boundary_violation")
    expected = {entry.path: entry for entry in capture.entries}
    actual = dict(_scan_mirror(mirror.root))
    if set(actual) != set(expected):
        _fail("diagnostic_boundary_violation")
    for relative, entry in expected.items():
        size_bytes, digest = _hash_file(actual[relative])
        if size_bytes != entry.size_bytes or digest != entry.sha256:
            _fail("diagnostic_boundary_violation")


def mirror_cpython_runtime(capture: RuntimeCapture, root: Path) -> RuntimeMirror:
    if type(capture) is not RuntimeCapture or not root.is_absolute():
        _fail("diagnostic_boundary_violation")
    try:
        with os.scandir(root) as iterator:
            if next(iterator, None) is not None:
                _fail("diagnostic_boundary_violation")
        directories = sorted(
            {
                "/".join(entry.path.split("/")[:depth])
                for entry in capture.entries
                for depth in range(1, len(entry.path.split("/")))
            },
            key=lambda item: (item.count("/"), item.encode("utf-8", "strict")),
        )
        for relative in directories:
            root.joinpath(*relative.split("/")).mkdir()
        for entry in capture.entries:
            destination = root.joinpath(*entry.path.split("/"))
            _copy_runtime_entry(entry, destination)
            os.chmod(destination, 0o555)
    except RuntimeDiagnosticError:
        raise
    except (OSError, RuntimeError):
        _fail("diagnostic_unavailable")
    entries = tuple(
        RuntimeEntry(
            item.path,
            item.size_bytes,
            item.sha256,
            root.joinpath(*item.path.split("/")),
        )
        for item in capture.entries
    )
    mirror = RuntimeMirror(
        entries,
        capture.runtime_digest,
        capture.canonical_size,
        capture.total_bytes,
        root,
        root / "python.exe",
    )
    prove_runtime_mirror(capture, mirror)
    return mirror


def _object_ref(runtime_digest: str, plane: str, component: str) -> str:
    if (
        _DIGEST.fullmatch(runtime_digest) is None
        or plane not in evidence.PLANE_ORDER
        or type(component) is not str
        or not component
        or "\0" in component
    ):
        _fail("diagnostic_boundary_violation")
    digest = hashlib.sha256()
    digest.update(OBJECT_REF_DOMAIN)
    for value in (runtime_digest, plane, component):
        digest.update(value.encode("utf-8", "strict"))
        digest.update(b"\0")
    return "inventory-sha256:" + digest.hexdigest()


def build_collector_inventory(
    capture: RuntimeCapture,
    *,
    system_images: tuple[str, ...] = (),
    known_dll_contracts: tuple[str, ...] = (),
    appcontainer_sid: str = "S-1-15-2-1-1",
) -> CollectorInventory:
    """Bind bounded logical targets without exposing source or mirror paths."""

    if (
        type(capture) is not RuntimeCapture
        or type(system_images) is not tuple
        or tuple(sorted(system_images)) != system_images
        or len(set(system_images)) != len(system_images)
        or any(
            type(name) is not str
            or name != name.casefold()
            or _COMPONENT.fullmatch(name) is None
            or not name.endswith(".dll")
            for name in system_images
        )
        or type(known_dll_contracts) is not tuple
        or tuple(sorted(known_dll_contracts)) != known_dll_contracts
        or len(set(known_dll_contracts)) != len(known_dll_contracts)
        or not set(known_dll_contracts).issubset(system_images)
        or any(
            _logical_dependency_identity(name) != name
            for name in known_dll_contracts
        )
        or type(appcontainer_sid) is not str
        or not appcontainer_sid.startswith("S-1-15-2-")
        or appcontainer_sid == lpac.ALL_APPLICATION_PACKAGES_SID
    ):
        _fail("diagnostic_boundary_violation")
    runtime_images = tuple(
        entry.path
        for entry in capture.entries
        if "/" not in entry.path
        and Path(entry.path).suffix.casefold() in {".exe", ".dll"}
    )
    entry_paths = {entry.path for entry in capture.entries}
    version = f"{capture.version[0]}.{capture.version[1]}"
    startup_candidates = (
        "Lib/codecs.py",
        "Lib/encodings/__init__.py",
        "Lib/encodings/aliases.py",
        "Lib/encodings/utf_8.py",
        "Lib/io.py",
        "Lib/abc.py",
    )
    startup_files = tuple(
        item for item in startup_candidates if item in entry_paths
    )
    if "python.exe" not in runtime_images or not any(
        item.startswith("python") and item.endswith(".dll")
        for item in runtime_images
    ):
        _fail("diagnostic_unavailable")
    runtime_file_components = tuple(
        f"runtime-relative:{item}"
        for item in sorted(set((*runtime_images, *startup_files)))
    )
    system_file_components = tuple(
        f"system32-basename:{item}" for item in system_images
    )
    attempt_file_components = (
        "attempt-root",
        "attempt-relative:runtime",
        "attempt-relative:scratch",
        "attempt-relative:scratch/home",
        "attempt-relative:scratch/local",
        "attempt-relative:scratch/roaming",
        "attempt-relative:scratch/tmp",
    )
    file_components = tuple(
        sorted(
            (
                *runtime_file_components,
                *system_file_components,
                *attempt_file_components,
            )
        )
    )
    image_components = tuple(
        sorted(
            (
                *(f"runtime-image:{item}" for item in runtime_images),
                *(f"system32-image:{item}" for item in system_images),
            )
        )
    )
    runtime_dependency_names: set[str] = set()
    for item in runtime_images:
        if Path(item).suffix.casefold() != ".dll":
            continue
        canonical = _logical_dependency_identity(item)
        if canonical is None or canonical in runtime_dependency_names:
            _fail("diagnostic_unavailable")
        runtime_dependency_names.add(canonical)
    physical_system_names = set(system_images)
    known_names = set(known_dll_contracts)
    if (
        runtime_dependency_names & physical_system_names
    ):
        _fail("diagnostic_unavailable")
    logical_dependency_origins = {
        **{name: "runtime" for name in runtime_dependency_names},
        **{name: "known_dll" for name in known_names},
    }
    logical_dependency_components = tuple(
        f"logical-dependency:{logical_dependency_origins[name]}:{name}"
        for name in sorted(logical_dependency_origins)
    )
    registry_components = (
        "registry-key:HKCU/Environment",
        f"registry-key:HKCU/Software/Python/PythonCore/{version}/PythonPath",
        f"registry-key:HKLM/Software/Python/PythonCore/{version}/PythonPath",
        "registry-key:HKLM/Software/Microsoft/Windows NT/CurrentVersion/Image File Execution Options/python.exe",
        "registry-key:HKLM/System/CurrentControlSet/Control/Session Manager/Environment",
        "registry-key:HKLM/System/CurrentControlSet/Control/Session Manager/KnownDLLs",
        f"registry-key:APPCONTAINER/{appcontainer_sid}/ProfileRoot",
    )
    components = {
        "file_access": file_components,
        "dll_image_load": tuple(
            sorted((*image_components, *logical_dependency_components))
        ),
        "registry_access": registry_components,
        "code_integrity_policy": image_components,
    }
    if any(
        not 1 <= len(items) <= evidence.INVENTORY_MAX_OBJECTS_PER_PLANE
        for items in components.values()
    ):
        _fail("diagnostic_unavailable")
    objects: list[CollectorObject] = []
    objects_by_plane: dict[str, tuple[str, ...]] = {}
    for plane in evidence.PLANE_ORDER:
        plane_objects = tuple(
            sorted(
                (
                    CollectorObject(
                        plane,
                        _object_ref(capture.runtime_digest, plane, component),
                        (
                            "exact_loader_dependency"
                            if _logical_dependency_component(component)
                            is not None
                            else {
                                "file_access": "exact_file_identity",
                                "dll_image_load": "exact_image_identity",
                                "registry_access": "canonical_registry_key",
                                "code_integrity_policy": "exact_ci_image_identity",
                            }[plane]
                        ),
                        component,
                        (
                            _logical_dependency_component(component)[0]
                            if _logical_dependency_component(component)
                            is not None
                            else None
                        ),
                    )
                    for component in components[plane]
                ),
                key=lambda item: item.object_ref,
            )
        )
        objects.extend(plane_objects)
        objects_by_plane[plane] = tuple(item.object_ref for item in plane_objects)
    manifest = evidence.bind_inventory_manifest(
        runtime_digest=capture.runtime_digest,
        objects_by_plane=objects_by_plane,
    )
    return CollectorInventory(capture.runtime_digest, manifest, tuple(objects))


def _system32_root() -> Path:
    buffer = ctypes.create_unicode_buffer(32_768)
    length = int(
        portability._apis().kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    )
    if length <= 0 or length >= len(buffer):
        _fail("diagnostic_unavailable")
    windows = Path(buffer.value)
    system32 = windows / "System32"
    if not windows.is_absolute() or not system32.is_dir() or _is_reparse(system32):
        _fail("diagnostic_unavailable")
    return system32


def _verified_system_image_basenames() -> tuple[str, ...]:
    """Bind only existing non-reparse System32 image basenames."""

    system32 = _system32_root()
    verified: list[str] = []
    for name in _SYSTEM_IMAGE_CANDIDATES:
        path = system32 / name
        try:
            observed = path.lstat()
            if not _is_reparse(path) and stat.S_ISREG(observed.st_mode):
                verified.append(name)
        except RuntimeDiagnosticError:
            raise
        except OSError:
            continue
    result = tuple(sorted(verified))
    if not {"kernel32.dll", "kernelbase.dll", "ntdll.dll"}.issubset(result):
        _fail("diagnostic_unavailable")
    return result


def _verified_known_dll_contracts(
    system_images: tuple[str, ...],
) -> tuple[str, ...]:
    """Read the bounded 64-bit KnownDLL contract and retain only held leaves."""

    if (
        type(system_images) is not tuple
        or not system_images
        or tuple(sorted(system_images)) != system_images
        or len(set(system_images)) != len(system_images)
    ):
        _fail("diagnostic_unavailable")
    try:
        import winreg

        query_value = winreg.KEY_QUERY_VALUE
        wow64_64 = winreg.KEY_WOW64_64KEY
        if (
            type(query_value) is not int
            or query_value != 0x0001
            or type(wow64_64) is not int
            or wow64_64 != 0x0100
        ):
            _fail("diagnostic_unavailable")
        access = query_value | wow64_64
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs",
            0,
            access,
        ) as key:
            def snapshot() -> tuple[
                tuple[int, int, int], tuple[tuple[str, str, int], ...]
            ]:
                before = winreg.QueryInfoKey(key)
                if (
                    type(before) is not tuple
                    or len(before) != 3
                    or any(type(value) is not int for value in before)
                    or before[0] < 0
                    or not 1 <= before[1] <= _KNOWN_DLL_VALUE_LIMIT
                    or before[2] < 0
                ):
                    _fail("diagnostic_unavailable")
                rows: list[tuple[str, str, int]] = []
                total_text = 0
                names: set[str] = set()
                for index in range(before[1]):
                    value_name, value_data, value_kind = winreg.EnumValue(
                        key, index
                    )
                    if (
                        type(value_name) is not str
                        or not value_name
                        or "\0" in value_name
                        or len(value_name) > 1_024
                        or type(value_data) is not str
                        or "\0" in value_data
                        or len(value_data) > 1_024
                        or type(value_kind) is not int
                        or value_kind != winreg.REG_SZ
                    ):
                        _fail("diagnostic_unavailable")
                    canonical_name = value_name.casefold()
                    if canonical_name in names:
                        _fail("diagnostic_unavailable")
                    names.add(canonical_name)
                    total_text += len(value_name) + len(value_data)
                    if total_text > _KNOWN_DLL_TEXT_LIMIT:
                        _fail("diagnostic_unavailable")
                    rows.append((value_name, value_data, value_kind))
                after = winreg.QueryInfoKey(key)
                if after != before:
                    _fail("diagnostic_unavailable")
                return before, tuple(sorted(rows))

            first_metadata, first = snapshot()
            second_metadata, second = snapshot()
            if first_metadata != second_metadata or first != second:
                _fail("diagnostic_unavailable")
            contracts: set[str] = set()
            observed_dll_data: set[str] = set()
            available = set(system_images)
            for _value_name, value_data, _value_kind in first:
                canonical = _logical_dependency_identity(value_data)
                if canonical is None:
                    continue
                if canonical in observed_dll_data:
                    _fail("diagnostic_unavailable")
                observed_dll_data.add(canonical)
                if canonical in available:
                    contracts.add(canonical)
    except RuntimeDiagnosticError:
        raise
    except BaseException:
        _fail("diagnostic_unavailable")
    result = tuple(sorted(contracts))
    if not {"kernel32.dll", "ntdll.dll"}.issubset(result):
        _fail("diagnostic_unavailable")
    return result


# The following binding is intentionally smaller than the inactive M24.2
# provider: it owns only no-follow filesystem DACL application/proof and the
# two process-lifecycle calls that the accepted seam deliberately omits.
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
HANDLE = ctypes.c_void_p
PSID = ctypes.c_void_p
LPVOID = ctypes.c_void_p

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_TAG_INFO = 9
FILE_STANDARD_INFO = 1
FILE_ID_INFO = 18
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_NAME_NORMALIZED = 0x0
VOLUME_NAME_DOS = 0x0
VOLUME_NAME_NT = 0x2
FINAL_PATH_BUFFER_CHARS = 32_768
CSTR_EQUAL = 2
OPEN_EXISTING = 3
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
WRITE_DAC = 0x00040000
WRITE_OWNER = 0x00080000
DELETE = 0x00010000
FILE_WRITE_DATA = 0x0002
FILE_APPEND_DATA = 0x0004
FILE_WRITE_EA = 0x0010
FILE_DELETE_CHILD = 0x0040
FILE_WRITE_ATTRIBUTES = 0x0100
OWNER_SECURITY_INFORMATION = 0x1
GROUP_SECURITY_INFORMATION = 0x2
DACL_SECURITY_INFORMATION = 0x4
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SE_FILE_OBJECT = 1
OWNER_RIGHTS_SID = "S-1-3-4"
SYSTEM_SID = "S-1-5-18"
EXACT_PACKAGE_MASK = lpac.FILE_GENERIC_READ | lpac.FILE_GENERIC_EXECUTE
_DENIED_FILE_RIGHTS = (
    FILE_WRITE_DATA,
    FILE_APPEND_DATA,
    FILE_WRITE_EA,
    FILE_WRITE_ATTRIBUTES,
    DELETE,
    FILE_DELETE_CHILD,
    WRITE_DAC,
    WRITE_OWNER,
)


class _FILE_ATTRIBUTE_TAG_INFORMATION(ctypes.Structure):
    _fields_ = (("FileAttributes", DWORD), ("ReparseTag", DWORD))


class _FILE_ID_128(ctypes.Structure):
    _fields_ = (("Identifier", wintypes.BYTE * 16),)


class _FILE_ID_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", _FILE_ID_128),
    )


class _FILE_STANDARD_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", DWORD),
        ("DeletePending", wintypes.BYTE),
        ("Directory", wintypes.BYTE),
    )


class _DaclApis:
    def __init__(self) -> None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            _fail("diagnostic_unavailable")
        try:
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            k = self.kernel32
            a = self.advapi32
            self._prototype(
                k.CreateFileW,
                [wintypes.LPCWSTR, DWORD, DWORD, LPVOID, DWORD, DWORD, HANDLE],
                HANDLE,
            )
            self._prototype(
                k.GetFileInformationByHandleEx,
                [HANDLE, ctypes.c_int, LPVOID, DWORD],
                wintypes.BOOL,
            )
            self._prototype(
                k.GetHandleInformation,
                [HANDLE, ctypes.POINTER(DWORD)],
                wintypes.BOOL,
            )
            self._prototype(
                k.GetFinalPathNameByHandleW,
                [HANDLE, wintypes.LPWSTR, DWORD, DWORD],
                DWORD,
            )
            self._prototype(k.CloseHandle, [HANDLE], wintypes.BOOL)
            self._prototype(
                k.CompareStringOrdinal,
                [
                    wintypes.LPCWSTR,
                    ctypes.c_int,
                    wintypes.LPCWSTR,
                    ctypes.c_int,
                    wintypes.BOOL,
                ],
                ctypes.c_int,
            )
            self._prototype(k.ResumeThread, [HANDLE], DWORD)
            self._prototype(k.WaitForSingleObject, [HANDLE, DWORD], DWORD)
            self._prototype(
                k.GetExitCodeProcess,
                [HANDLE, ctypes.POINTER(DWORD)],
                wintypes.BOOL,
            )
            self._prototype(
                a.GetSecurityInfo,
                [
                    HANDLE,
                    ctypes.c_int,
                    DWORD,
                    ctypes.POINTER(PSID),
                    ctypes.POINTER(PSID),
                    ctypes.POINTER(LPVOID),
                    ctypes.POINTER(LPVOID),
                    ctypes.POINTER(LPVOID),
                ],
                DWORD,
            )
            self._prototype(
                a.SetSecurityInfo,
                [HANDLE, ctypes.c_int, DWORD, PSID, PSID, LPVOID, LPVOID],
                DWORD,
            )
            self._prototype(a.GetSecurityDescriptorLength, [LPVOID], DWORD)
        except (AttributeError, OSError):
            _fail("diagnostic_unavailable")

    @staticmethod
    def _prototype(function: object, arguments: list[object], result: object) -> None:
        function.argtypes = arguments
        function.restype = result


_DACL_API: _DaclApis | None = None


def _dacl_apis() -> _DaclApis:
    global _DACL_API
    if _DACL_API is None:
        _DACL_API = _DaclApis()
    return _DACL_API


def _windows_ordinal_equal(first: str, second: str) -> bool:
    if (
        type(first) is not str
        or type(second) is not str
        or not first
        or not second
        or len(first) > 32_768
        or len(second) > 32_768
        or "\0" in first
        or "\0" in second
    ):
        _fail("diagnostic_collector_failed")
    result = int(
        _dacl_apis().kernel32.CompareStringOrdinal(
            first, -1, second, -1, True
        )
    )
    if result == 0:
        _fail("diagnostic_collector_failed")
    return result == CSTR_EQUAL


@dataclass(frozen=True, slots=True)
class _TreeItem:
    path: Path = field(repr=False)
    relative: str
    is_directory: bool


@dataclass(frozen=True, slots=True)
class _ExactDaclProof:
    owner_sid: str
    trustees: tuple[str, str, str, str]
    masks: tuple[int, int, int, int]
    descriptor_bytes: int


class _ExactFilesystemDescriptor:
    def __init__(self, user_sid: str, appcontainer_sid: str) -> None:
        trustees = (SYSTEM_SID, user_sid, OWNER_RIGHTS_SID, appcontainer_sid)
        masks = (
            lpac.FILE_ALL_ACCESS,
            lpac.FILE_ALL_ACCESS,
            lpac.READ_CONTROL,
            EXACT_PACKAGE_MASK,
        )
        self._sids: list[lpac._OwnedLocalSid] = []
        self.pointer = LPVOID()
        self.dacl = LPVOID()
        try:
            for value in (user_sid, *trustees):
                self._sids.append(lpac._OwnedLocalSid(value))
            acl_size = 8 + sum(
                8 + int(lpac._apis().advapi32.GetLengthSid(item.pointer))
                for item in self._sids[1:]
            )
            self._acl = ctypes.create_string_buffer(acl_size)
            self.dacl = ctypes.cast(self._acl, LPVOID)
            if not lpac._apis().advapi32.InitializeAcl(self.dacl, acl_size, 2):
                _fail("diagnostic_boundary_violation")
            for sid, mask in zip(self._sids[1:], masks, strict=True):
                if not lpac._apis().advapi32.AddAccessAllowedAceEx(
                    self.dacl, 2, 0, mask, sid.pointer
                ):
                    _fail("diagnostic_boundary_violation")
            self._descriptor = ctypes.create_string_buffer(
                ctypes.sizeof(lpac._SECURITY_DESCRIPTOR)
            )
            self.pointer = ctypes.cast(self._descriptor, LPVOID)
            if (
                not lpac._apis().advapi32.InitializeSecurityDescriptor(
                    self.pointer, 1
                )
                or not lpac._apis().advapi32.SetSecurityDescriptorOwner(
                    self.pointer, self._sids[0].pointer, False
                )
                or not lpac._apis().advapi32.SetSecurityDescriptorDacl(
                    self.pointer, True, self.dacl, False
                )
            ):
                _fail("diagnostic_boundary_violation")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.pointer = LPVOID()
        self.dacl = LPVOID()
        cleanup_failed = False
        for sid in getattr(self, "_sids", ()):
            try:
                sid.close()
            except BaseException:
                cleanup_failed = True
        self._sids = []
        if cleanup_failed:
            _fail("diagnostic_cleanup_failed")

    def __enter__(self) -> "_ExactFilesystemDescriptor":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _native_path(path: Path) -> str:
    value = str(path.absolute())
    if not path.is_absolute() or "\0" in value:
        _fail("diagnostic_boundary_violation")
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


class _HeldNativeHandle:
    __slots__ = ("_value",)

    def __init__(self, value: object) -> None:
        raw = int(value.value or 0) if isinstance(value, ctypes.c_void_p) else int(value or 0)
        if raw in {0, INVALID_HANDLE_VALUE}:
            _fail("diagnostic_boundary_violation")
        self._value = raw

    @property
    def value(self) -> int:
        if self._value == 0:
            _fail("diagnostic_boundary_violation")
        return self._value

    @property
    def closed(self) -> bool:
        return self._value == 0

    def close(self) -> None:
        if self._value == 0:
            return
        if not _dacl_apis().kernel32.CloseHandle(HANDLE(self._value)):
            _fail("diagnostic_cleanup_failed")
        self._value = 0


class _HeldAliasLease(_AliasLease):
    """Retain the exact no-follow handle which authorized two path aliases."""

    __slots__ = (
        "_handle",
        "_is_directory",
        "_reproved",
        "aliases",
        "file_identity",
    )

    def __init__(
        self,
        handle: _HeldNativeHandle,
        aliases: tuple[str, str],
        file_identity: tuple[int, bytes],
        is_directory: bool,
    ) -> None:
        self._handle = handle
        self._is_directory = is_directory
        self._reproved = False
        self.aliases = aliases
        self.file_identity = file_identity

    def reprove(self) -> None:
        if self._reproved or self._handle.closed:
            _fail("diagnostic_collector_failed")
        _held_leaf_attributes(self._handle, is_directory=self._is_directory)
        _prove_noninheritable(self._handle)
        before = _held_file_identity(self._handle)
        dos_path = _held_final_path(self._handle, VOLUME_NAME_DOS)
        nt_path = _held_final_path(self._handle, VOLUME_NAME_NT)
        after = _held_file_identity(self._handle)
        if (
            before != self.file_identity
            or after != self.file_identity
            or not _windows_ordinal_equal(dos_path, self.aliases[0])
            or not _windows_ordinal_equal(nt_path, self.aliases[1])
        ):
            _fail("diagnostic_collector_failed")
        self._reproved = True

    def close(self) -> None:
        self.aliases = ("", "")
        self.file_identity = (0, b"")
        if not self._handle.closed:
            try:
                self._handle.close()
            except BaseException:
                _fail("diagnostic_cleanup_failed")


def _held_file_identity(handle: _HeldNativeHandle) -> tuple[int, bytes]:
    if (
        ctypes.sizeof(_FILE_ID_128) != 16
        or ctypes.sizeof(_FILE_ID_INFORMATION) != 24
        or _FILE_ID_INFORMATION.VolumeSerialNumber.offset != 0
        or _FILE_ID_INFORMATION.FileId.offset != 8
    ):
        _fail("diagnostic_boundary_violation")
    information = _FILE_ID_INFORMATION()
    if not _dacl_apis().kernel32.GetFileInformationByHandleEx(
        HANDLE(handle.value),
        FILE_ID_INFO,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        _fail("diagnostic_boundary_violation")
    identity = (
        int(information.VolumeSerialNumber),
        bytes(information.FileId.Identifier),
    )
    if identity[0] <= 0 or len(identity[1]) != 16 or not any(identity[1]):
        _fail("diagnostic_boundary_violation")
    return identity


def _held_leaf_attributes(
    handle: _HeldNativeHandle, *, is_directory: bool
) -> _FILE_ATTRIBUTE_TAG_INFORMATION:
    if (
        ctypes.sizeof(_FILE_STANDARD_INFORMATION) != 24
        or _FILE_STANDARD_INFORMATION.AllocationSize.offset != 0
        or _FILE_STANDARD_INFORMATION.EndOfFile.offset != 8
        or _FILE_STANDARD_INFORMATION.NumberOfLinks.offset != 16
        or _FILE_STANDARD_INFORMATION.DeletePending.offset != 20
        or _FILE_STANDARD_INFORMATION.Directory.offset != 21
    ):
        _fail("diagnostic_boundary_violation")
    attributes = _FILE_ATTRIBUTE_TAG_INFORMATION()
    standard = _FILE_STANDARD_INFORMATION()
    for information_class, value in (
        (FILE_ATTRIBUTE_TAG_INFO, attributes),
        (FILE_STANDARD_INFO, standard),
    ):
        if not _dacl_apis().kernel32.GetFileInformationByHandleEx(
            HANDLE(handle.value),
            information_class,
            ctypes.byref(value),
            ctypes.sizeof(value),
        ):
            _fail("diagnostic_boundary_violation")
    if (
        bool(attributes.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
        is not is_directory
        or attributes.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT
        or int(attributes.ReparseTag) != 0
        or bool(standard.Directory) is not is_directory
        or bool(standard.DeletePending)
        or int(standard.NumberOfLinks) < 1
    ):
        _fail("diagnostic_boundary_violation")
    return attributes


def _prove_noninheritable(handle: _HeldNativeHandle) -> None:
    handle_flags = DWORD()
    if (
        not _dacl_apis().kernel32.GetHandleInformation(
            HANDLE(handle.value), ctypes.byref(handle_flags)
        )
        or handle_flags.value & lpac.HANDLE_FLAG_INHERIT
    ):
        _fail("diagnostic_boundary_violation")


def _held_final_path(handle: _HeldNativeHandle, volume_kind: int) -> str:
    if volume_kind not in {VOLUME_NAME_DOS, VOLUME_NAME_NT}:
        _fail("diagnostic_boundary_violation")
    buffer = ctypes.create_unicode_buffer(FINAL_PATH_BUFFER_CHARS)
    length = int(
        _dacl_apis().kernel32.GetFinalPathNameByHandleW(
            HANDLE(handle.value),
            buffer,
            len(buffer),
            FILE_NAME_NORMALIZED | volume_kind,
        )
    )
    if (
        length <= 0
        or length >= len(buffer)
        or buffer[length] != "\0"
    ):
        _fail("diagnostic_boundary_violation")
    value = buffer.value
    try:
        utf16_units = len(value.encode("utf-16-le", "strict")) // 2
    except UnicodeError:
        _fail("diagnostic_boundary_violation")
    if length != utf16_units:
        _fail("diagnostic_boundary_violation")
    identity = (
        _path_identity(value)
        if volume_kind == VOLUME_NAME_DOS
        else _nt_path_identity(value)
    )
    if identity is None:
        _fail("diagnostic_boundary_violation")
    return value


def _open_trusted_alias_lease(
    path: Path, *, is_directory: bool
) -> _HeldAliasLease:
    if not path.is_absolute() or type(is_directory) is not bool:
        _fail("diagnostic_boundary_violation")
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if is_directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    raw = _dacl_apis().kernel32.CreateFileW(
        _native_path(path),
        lpac.FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if int(raw or 0) in {0, INVALID_HANDLE_VALUE}:
        _fail("diagnostic_boundary_violation")
    handle = _HeldNativeHandle(raw)
    lease = _HeldAliasLease(handle, ("", ""), (0, b""), is_directory)
    try:
        _held_leaf_attributes(handle, is_directory=is_directory)
        _prove_noninheritable(handle)
        before = _held_file_identity(handle)
        dos_path = _held_final_path(handle, VOLUME_NAME_DOS)
        nt_path = _held_final_path(handle, VOLUME_NAME_NT)
        after = _held_file_identity(handle)
        if before != after:
            _fail("diagnostic_boundary_violation")
        lease.aliases = (dos_path, nt_path)
        lease.file_identity = before
    except BaseException:
        # Return the invalid but still-owned lease.  The resolver records it
        # before shape validation so adapter cleanup can close or retry it.
        pass
    return lease


@contextmanager
def _open_tree_item(item: _TreeItem, *, writable_dacl: bool) -> Iterator[lpac.OwnedHandle]:
    access = lpac.READ_CONTROL | lpac.FILE_READ_ATTRIBUTES
    if writable_dacl:
        access |= WRITE_DAC
    flags = FILE_FLAG_OPEN_REPARSE_POINT
    if item.is_directory:
        flags |= FILE_FLAG_BACKUP_SEMANTICS
    raw = _dacl_apis().kernel32.CreateFileW(
        _native_path(item.path),
        access,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        flags,
        None,
    )
    if int(raw or 0) in {0, INVALID_HANDLE_VALUE}:
        _fail("diagnostic_boundary_violation")
    handle = lpac.OwnedHandle(raw)
    try:
        information = _FILE_ATTRIBUTE_TAG_INFORMATION()
        if not _dacl_apis().kernel32.GetFileInformationByHandleEx(
            HANDLE(handle.value),
            FILE_ATTRIBUTE_TAG_INFO,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            _fail("diagnostic_boundary_violation")
        if (
            bool(information.FileAttributes & FILE_ATTRIBUTE_DIRECTORY)
            is not item.is_directory
            or information.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            _fail("diagnostic_boundary_violation")
        flags_value = DWORD()
        if (
            not _dacl_apis().kernel32.GetHandleInformation(
                HANDLE(handle.value), ctypes.byref(flags_value)
            )
            or flags_value.value & lpac.HANDLE_FLAG_INHERIT
        ):
            _fail("diagnostic_boundary_violation")
        yield handle
    finally:
        if not handle.closed:
            handle.close()


def _enumerate_tree(root: Path, *, deadline: float) -> tuple[_TreeItem, ...]:
    if not root.is_absolute():
        _fail("diagnostic_boundary_violation")
    root_identity = _observe_directory(root)
    result = [_TreeItem(root, "", True)]
    stack = [(root, "")]
    while stack:
        if time.monotonic() > deadline:
            _fail("diagnostic_boundary_violation")
        directory, relative = stack.pop()
        children: list[os.DirEntry[str]] = []
        try:
            with os.scandir(directory) as iterator:
                children.extend(iterator)
            children.sort(key=lambda item: item.name.encode("utf-8", "strict"))
        except (OSError, RuntimeError, UnicodeError):
            _fail("diagnostic_boundary_violation")
        directories: list[tuple[Path, str]] = []
        for child in children:
            path = directory / child.name
            child_relative = child.name if not relative else f"{relative}/{child.name}"
            _validate_relative_path(child_relative)
            try:
                if child.is_symlink() or _is_reparse(path):
                    _fail("diagnostic_boundary_violation")
                if child.is_dir(follow_symlinks=False):
                    item = _TreeItem(path, child_relative, True)
                    directories.append((path, child_relative))
                elif child.is_file(follow_symlinks=False):
                    item = _TreeItem(path, child_relative, False)
                else:
                    _fail("diagnostic_boundary_violation")
            except RuntimeDiagnosticError:
                raise
            except (OSError, RuntimeError):
                _fail("diagnostic_boundary_violation")
            result.append(item)
            if len(result) > TREE_ENTRY_LIMIT:
                _fail("diagnostic_boundary_violation")
        stack.extend(reversed(directories))
    if not _same_directory(root_identity, _observe_directory(root)):
        _fail("diagnostic_boundary_violation")
    result.sort(key=lambda item: item.relative.encode("utf-8", "strict"))
    return tuple(result)


def _security_info(
    handle: lpac.OwnedHandle,
) -> tuple[LPVOID, PSID, PSID, LPVOID]:
    owner = PSID()
    group = PSID()
    dacl = LPVOID()
    descriptor = LPVOID()
    result = _dacl_apis().advapi32.GetSecurityInfo(
        HANDLE(handle.value),
        SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION
        | GROUP_SECURITY_INFORMATION
        | DACL_SECURITY_INFORMATION,
        ctypes.byref(owner),
        ctypes.byref(group),
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not descriptor or not owner or not group or not dacl:
        if descriptor:
            lpac._local_free(descriptor)
        _fail("diagnostic_boundary_violation")
    return descriptor, owner, group, dacl


def _expected_dacl(
    user_sid: str, appcontainer_sid: str
) -> tuple[tuple[str, str, str, str], tuple[int, int, int, int]]:
    return (
        (SYSTEM_SID, user_sid, OWNER_RIGHTS_SID, appcontainer_sid),
        (
            lpac.FILE_ALL_ACCESS,
            lpac.FILE_ALL_ACCESS,
            lpac.READ_CONTROL,
            EXACT_PACKAGE_MASK,
        ),
    )


def _prove_exact_dacl(
    handle: lpac.OwnedHandle, *, user_sid: str, appcontainer_sid: str
) -> _ExactDaclProof:
    descriptor, owner, group, dacl = _security_info(handle)
    try:
        control = WORD()
        revision = DWORD()
        if (
            not lpac._apis().advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            )
            or not control.value & lpac.SE_DACL_PROTECTED
            or lpac._sid_to_string(owner) != user_sid
            or not lpac._sid_to_string(group).startswith("S-1-")
        ):
            _fail("diagnostic_boundary_violation")
        information = lpac._ACL_SIZE_INFORMATION()
        if (
            not lpac._apis().advapi32.GetAclInformation(
                dacl,
                ctypes.byref(information),
                ctypes.sizeof(information),
                lpac.ACL_SIZE_INFORMATION,
            )
            or information.AceCount != 4
        ):
            _fail("diagnostic_boundary_violation")
        trustees: list[str] = []
        masks: list[int] = []
        for index in range(4):
            raw_ace = LPVOID()
            if (
                not lpac._apis().advapi32.GetAce(
                    dacl, index, ctypes.byref(raw_ace)
                )
                or not raw_ace
            ):
                _fail("diagnostic_boundary_violation")
            header = lpac._ACE_HEADER.from_address(raw_ace.value)
            if (
                header.AceType != lpac.ACCESS_ALLOWED_ACE_TYPE
                or header.AceFlags != 0
                or header.AceSize < 12
            ):
                _fail("diagnostic_boundary_violation")
            mask = int(DWORD.from_address(raw_ace.value + 4).value)
            sid = PSID(raw_ace.value + 8)
            sid_length = int(lpac._apis().advapi32.GetLengthSid(sid))
            if sid_length <= 0 or header.AceSize != 8 + sid_length:
                _fail("diagnostic_boundary_violation")
            trustees.append(lpac._sid_to_string(sid))
            masks.append(mask)
        expected_trustees, expected_masks = _expected_dacl(
            user_sid, appcontainer_sid
        )
        if tuple(trustees) != expected_trustees or tuple(masks) != expected_masks:
            _fail("diagnostic_boundary_violation")
        descriptor_bytes = int(
            _dacl_apis().advapi32.GetSecurityDescriptorLength(descriptor)
        )
        if descriptor_bytes <= 0:
            _fail("diagnostic_boundary_violation")
        return _ExactDaclProof(
            user_sid,
            expected_trustees,
            expected_masks,
            descriptor_bytes,
        )
    finally:
        lpac._local_free(descriptor)


def _apply_exact_dacl(
    handle: lpac.OwnedHandle,
    descriptor: _ExactFilesystemDescriptor,
    *,
    user_sid: str,
    appcontainer_sid: str,
) -> _ExactDaclProof:
    current, owner, group, _dacl = _security_info(handle)
    try:
        if (
            lpac._sid_to_string(owner) != user_sid
            or not lpac._sid_to_string(group).startswith("S-1-")
        ):
            _fail("diagnostic_boundary_violation")
    finally:
        lpac._local_free(current)
    result = _dacl_apis().advapi32.SetSecurityInfo(
        HANDLE(handle.value),
        SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        descriptor.dacl,
        None,
    )
    if result != 0:
        _fail("diagnostic_boundary_violation")
    return _prove_exact_dacl(
        handle, user_sid=user_sid, appcontainer_sid=appcontainer_sid
    )


def _tree_proof(
    records: list[tuple[str, _ExactDaclProof]], descriptor_bytes: int
) -> TreeDaclProof:
    digest = hashlib.sha256()
    digest.update(TREE_PROOF_DOMAIN)
    for relative, proof in sorted(
        records, key=lambda item: item[0].encode("utf-8", "strict")
    ):
        digest.update(relative.encode("utf-8", "strict"))
        digest.update(b"\0")
        digest.update(proof.owner_sid.encode("ascii", "strict"))
        for sid, mask in zip(proof.trustees, proof.masks, strict=True):
            digest.update(b"\0")
            digest.update(sid.encode("ascii", "strict"))
            digest.update(mask.to_bytes(4, "little"))
    return TreeDaclProof(
        len(records), descriptor_bytes, "sha256:" + digest.hexdigest()
    )


def seal_runtime_tree(root: Path, appcontainer_sid: str) -> TreeDaclProof:
    deadline = time.monotonic() + TREE_TIMEOUT_SECONDS
    user_sid = lpac._current_user_sid()
    first = _enumerate_tree(root, deadline=deadline)
    records: list[tuple[str, _ExactDaclProof]] = []
    descriptor_bytes = 0
    with _ExactFilesystemDescriptor(user_sid, appcontainer_sid) as descriptor:
        for item in sorted(
            first,
            key=lambda value: (
                value.relative.count("/") + 1 if value.relative else 0
            ),
            reverse=True,
        ):
            if time.monotonic() > deadline:
                _fail("diagnostic_boundary_violation")
            with _open_tree_item(item, writable_dacl=True) as handle:
                proof = _apply_exact_dacl(
                    handle,
                    descriptor,
                    user_sid=user_sid,
                    appcontainer_sid=appcontainer_sid,
                )
            descriptor_bytes += proof.descriptor_bytes
            if descriptor_bytes > TREE_DESCRIPTOR_LIMIT:
                _fail("diagnostic_boundary_violation")
            records.append((item.relative, proof))
    second = _enumerate_tree(root, deadline=deadline)
    if tuple((item.relative, item.is_directory) for item in first) != tuple(
        (item.relative, item.is_directory) for item in second
    ):
        _fail("diagnostic_boundary_violation")
    reproof = prove_runtime_tree(root, appcontainer_sid, _deadline=deadline)
    proof = _tree_proof(records, descriptor_bytes)
    if reproof != proof:
        _fail("diagnostic_boundary_violation")
    return proof


def _seal_exact_runtime_scope(
    mirror: RuntimeMirror, appcontainer_sid: str
) -> TreeDaclProof:
    """The sole Package-SID filesystem mutation authorized by M24.1B."""

    if type(mirror) is not RuntimeMirror:
        _fail("diagnostic_boundary_violation")
    return seal_runtime_tree(mirror.root, appcontainer_sid)


def prove_runtime_tree(
    root: Path, appcontainer_sid: str, *, _deadline: float | None = None
) -> TreeDaclProof:
    deadline = _deadline or time.monotonic() + TREE_TIMEOUT_SECONDS
    user_sid = lpac._current_user_sid()
    records: list[tuple[str, _ExactDaclProof]] = []
    descriptor_bytes = 0
    for item in _enumerate_tree(root, deadline=deadline):
        if time.monotonic() > deadline:
            _fail("diagnostic_boundary_violation")
        with _open_tree_item(item, writable_dacl=False) as handle:
            proof = _prove_exact_dacl(
                handle, user_sid=user_sid, appcontainer_sid=appcontainer_sid
            )
        descriptor_bytes += proof.descriptor_bytes
        if descriptor_bytes > TREE_DESCRIPTOR_LIMIT:
            _fail("diagnostic_boundary_violation")
        records.append((item.relative, proof))
    return _tree_proof(records, descriptor_bytes)


def _access_allowed(
    descriptor: LPVOID, token: lpac.OwnedHandle, desired: int
) -> bool:
    mapping = lpac._GENERIC_MAPPING(
        lpac.FILE_GENERIC_READ,
        lpac.FILE_GENERIC_WRITE,
        lpac.FILE_GENERIC_EXECUTE,
        lpac.FILE_ALL_ACCESS,
    )
    lpac._query_exact_token_dword(
        token, lpac.TOKEN_TYPE, lpac.TOKEN_TYPE_IMPERSONATION
    )
    lpac._query_exact_token_dword(
        token, lpac.TOKEN_IMPERSONATION_LEVEL, lpac.SECURITY_IMPERSONATION
    )
    mapping_before = tuple(
        int(getattr(mapping, name))
        for name in ("GenericRead", "GenericWrite", "GenericExecute", "GenericAll")
    )
    if mapping_before != (
        lpac.FILE_GENERIC_READ,
        lpac.FILE_GENERIC_WRITE,
        lpac.FILE_GENERIC_EXECUTE,
        lpac.FILE_ALL_ACCESS,
    ):
        _fail("diagnostic_boundary_violation")
    privilege = lpac._PRIVILEGE_SET_ONE()
    privilege_length = DWORD(ctypes.sizeof(privilege))
    granted = DWORD()
    allowed = wintypes.BOOL()
    if not lpac._apis().advapi32.AccessCheck(
        descriptor,
        HANDLE(token.value),
        desired,
        ctypes.byref(mapping),
        ctypes.byref(privilege),
        ctypes.byref(privilege_length),
        ctypes.byref(granted),
        ctypes.byref(allowed),
    ):
        _fail("diagnostic_boundary_violation")
    mapping_after = tuple(
        int(getattr(mapping, name))
        for name in ("GenericRead", "GenericWrite", "GenericExecute", "GenericAll")
    )
    if mapping_after != mapping_before:
        _fail("diagnostic_boundary_violation")
    return lpac._validate_access_check_result(
        lpac._AccessCheckResult(
            int(privilege_length.value),
            int(privilege.PrivilegeCount),
            int(privilege.Control),
            int(privilege.Privilege[0].Luid.LowPart),
            int(privilege.Privilege[0].Luid.HighPart),
            int(privilege.Privilege[0].Attributes),
            int(granted.value),
            int(allowed.value),
        ),
        desired=desired,
    )


def prove_runtime_tree_effective_access(
    root: Path, appcontainer_sid: str, primary_token: lpac.OwnedHandle
) -> None:
    duplicate: lpac.OwnedHandle | None = None
    try:
        duplicate = lpac.duplicate_impersonation_token(primary_token)
        deadline = time.monotonic() + TREE_TIMEOUT_SECONDS
        for item in _enumerate_tree(root, deadline=deadline):
            with _open_tree_item(item, writable_dacl=False) as handle:
                descriptor, _owner, _group, _dacl = _security_info(handle)
                try:
                    for right in _DENIED_FILE_RIGHTS:
                        if _access_allowed(descriptor, duplicate, right):
                            _fail("diagnostic_boundary_violation")
                    if not _access_allowed(
                        descriptor, duplicate, EXACT_PACKAGE_MASK
                    ):
                        _fail("diagnostic_boundary_violation")
                finally:
                    lpac._local_free(descriptor)
    finally:
        if duplicate is not None and not duplicate.closed:
            duplicate.close()


class _DiagnosticContext(Protocol):
    application: Path
    inventory: CollectorInventory
    runtime_digest: str
    dacl_proof: TreeDaclProof

    def create_suspended_child(self) -> object: ...

    def prove_route_and_control(self, child: object) -> tuple[str, bool]: ...

    def prove_runtime_access(self, child: object) -> None: ...

    def close_child_security(self, child: object) -> None: ...

    def resume_once(self, child: object) -> None: ...

    def wait_access_denied(self, child: object) -> None: ...

    def finish_child_and_reprove(self, child: object) -> None: ...

    def close(self) -> DiagnosticCleanupProof: ...


class _RealDiagnosticContext:
    """Own every native resource for exactly one disposable diagnosis."""

    def __init__(self) -> None:
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._temporary_path: Path | None = None
        self._capture: RuntimeCapture | None = None
        self._mirror: RuntimeMirror | None = None
        self._profile = portability._FixtureProfile()
        self._child_job: portability._FixtureJob | None = None
        self._child_stdio: portability._FixtureStdio | None = None
        self._child: lpac.SuspendedLpacChild | None = None
        self._control_job: portability._FixtureJob | None = None
        self._control_stdio: portability._FixtureStdio | None = None
        self._control: lpac.SuspendedNormalControl | None = None
        self._child_job_zero = True
        self._control_job_zero = True
        self._control_created = False
        self._resume_count = 0
        self._all_handles_closed = True
        self._profile_absent = False
        self._temporary_absent = False
        self._finished = False
        self.application = Path()
        self.inventory: CollectorInventory
        self.runtime_digest = ""
        self.dacl_proof = TreeDaclProof(0, 0, "")

    @classmethod
    def create(cls) -> "_RealDiagnosticContext":
        context = cls()
        try:
            context._temporary = tempfile.TemporaryDirectory(
                prefix="taskgov-m241b-runtime-"
            )
            context._temporary_path = Path(context._temporary.name).resolve(
                strict=True
            )
            runtime_root = context._temporary_path / "runtime"
            scratch = context._temporary_path / "scratch"
            for directory in (runtime_root, scratch):
                directory.mkdir()
            context._capture = capture_current_cpython_runtime()
            context._mirror = mirror_cpython_runtime(
                context._capture, runtime_root
            )
            context._profile.create()
            system_images = _verified_system_image_basenames()
            context.inventory = build_collector_inventory(
                context._capture,
                system_images=system_images,
                known_dll_contracts=_verified_known_dll_contracts(
                    system_images
                ),
                appcontainer_sid=context._profile.sid,
            )
            environment = portability._environment_block(str(scratch))
            context.dacl_proof = _seal_exact_runtime_scope(
                context._mirror, context._profile.sid
            )
            prove_runtime_mirror(context._capture, context._mirror)
            arguments = build_diagnostic_argv(context._mirror.executable)
            context._spec = lpac.SuspendedLaunchSpec(
                str(context._mirror.executable),
                subprocess.list2cmdline(arguments),
                environment,
                str(context._mirror.root),
                context._profile.sid,
            )
            context.application = context._mirror.executable
            context.runtime_digest = context._capture.runtime_digest
            return context
        except BaseException as error:
            cleanup_error: BaseException | None = None
            cleanup_proof: DiagnosticCleanupProof | None = None
            try:
                cleanup_proof = context.close()
            except BaseException as caught:
                cleanup_error = caught
            if (
                cleanup_error is not None
                or cleanup_proof
                != DiagnosticCleanupProof(True, True, True, True, True)
            ):
                raise RuntimeDiagnosticError("diagnostic_cleanup_failed") from None
            raise _normalized_error(error) from None

    def create_suspended_child(self) -> lpac.SuspendedLpacChild:
        if self._child is not None:
            _fail("diagnostic_boundary_violation")
        self._child_job = portability._FixtureJob.create()
        self._child_job_zero = False
        self._child_stdio = portability._FixtureStdio.create()
        self._all_handles_closed = False
        self._child = lpac.create_suspended_lpac_child(
            self._spec, self._child_stdio.resources(self._child_job)
        )
        if not self._child_job.contains(self._child.process):
            _fail("diagnostic_boundary_violation")
        return self._child

    def _cleanup_control(self) -> None:
        cleanup_failed = False
        if self._control_job is not None and not self._control_job.handle.closed:
            try:
                proof = self._control_job.terminate_and_prove_zero()
                self._control_job_zero = proof.active_processes == 0
            except BaseException:
                cleanup_failed = True
        if self._control is not None:
            try:
                self._control.close()
            except BaseException:
                cleanup_failed = True
        if self._control_stdio is not None:
            try:
                self._control_stdio.close()
            except BaseException:
                cleanup_failed = True
        if self._control_job is not None:
            try:
                self._control_job.close()
            except BaseException:
                cleanup_failed = True
        if self._control is not None and not self._control.closed:
            cleanup_failed = True
        if self._control_stdio is not None and not self._control_stdio.closed:
            cleanup_failed = True
        if self._control_job is not None and not self._control_job.handle.closed:
            cleanup_failed = True
        self._control = None
        self._control_stdio = None
        self._control_job = None
        if cleanup_failed or not self._control_job_zero:
            _fail("diagnostic_cleanup_failed")

    def prove_route_and_control(
        self, child: object
    ) -> tuple[str, bool]:
        if child is not self._child or not isinstance(child, lpac.SuspendedLpacChild):
            _fail("diagnostic_boundary_violation")
        proof = lpac.prove_lpac_route(child.primary_token, self._profile.sid)
        if proof.route == lpac.LPAC_PROOF_ACCESS_CHECK:
            self._control_created = True
            self._control_job = portability._FixtureJob.create()
            self._control_job_zero = False
            self._control_stdio = portability._FixtureStdio.create()
            self._control = lpac.create_suspended_normal_control(
                self._spec, self._control_stdio.resources(self._control_job)
            )
            if not self._control_job.contains(self._control.process):
                _fail("diagnostic_boundary_violation")
            lpac.prove_access_check_semantics(
                self._control, child, self._profile.sid
            )
            self._cleanup_control()
        elif proof.route != lpac.LPAC_PROOF_CLASS_46:
            _fail("diagnostic_boundary_violation")
        return proof.route, self._control_created

    def prove_runtime_access(self, child: object) -> None:
        if (
            child is not self._child
            or self._capture is None
            or self._mirror is None
        ):
            _fail("diagnostic_boundary_violation")
        prove_runtime_tree_effective_access(
            self._mirror.root, self._profile.sid, self._child.primary_token
        )
        if prove_runtime_tree(self._mirror.root, self._profile.sid) != self.dacl_proof:
            _fail("diagnostic_boundary_violation")
        prove_runtime_mirror(self._capture, self._mirror)

    def close_child_security(self, child: object) -> None:
        if child is not self._child or self._child.primary_token.closed:
            _fail("diagnostic_boundary_violation")
        self._child.primary_token.close()
        if not self._child.primary_token.closed:
            _fail("diagnostic_cleanup_failed")

    def resume_once(self, child: object) -> None:
        if (
            child is not self._child
            or self._resume_count != 0
            or self._child.thread.closed
            or not self._child.primary_token.closed
            or self._control is not None
        ):
            _fail("diagnostic_boundary_violation")
        previous = int(
            _dacl_apis().kernel32.ResumeThread(HANDLE(self._child.thread.value))
        )
        if previous != 1:
            _fail("diagnostic_boundary_violation")
        self._resume_count = 1
        self._child.thread.close()

    def wait_access_denied(self, child: object) -> None:
        if child is not self._child or self._resume_count != 1:
            _fail("diagnostic_boundary_violation")
        wait_result = int(
            _dacl_apis().kernel32.WaitForSingleObject(
                HANDLE(self._child.process.value), CHILD_WAIT_MILLISECONDS
            )
        )
        if wait_result == WAIT_TIMEOUT:
            _fail("diagnostic_baseline_mismatch")
        if wait_result != WAIT_OBJECT_0:
            _fail("diagnostic_boundary_violation")
        status = DWORD()
        if not _dacl_apis().kernel32.GetExitCodeProcess(
            HANDLE(self._child.process.value), ctypes.byref(status)
        ):
            _fail("diagnostic_boundary_violation")
        if int(status.value) != STATUS_ACCESS_DENIED:
            _fail("diagnostic_baseline_mismatch")

    def _cleanup_child(self) -> None:
        cleanup_failed = False
        if self._child_job is not None and not self._child_job.handle.closed:
            try:
                proof = self._child_job.terminate_and_prove_zero()
                self._child_job_zero = proof.active_processes == 0
            except BaseException:
                cleanup_failed = True
        if self._child is not None:
            try:
                self._child.close()
            except BaseException:
                cleanup_failed = True
        if self._child_stdio is not None:
            try:
                self._child_stdio.close()
            except BaseException:
                cleanup_failed = True
        if self._child_job is not None:
            try:
                self._child_job.close()
            except BaseException:
                cleanup_failed = True
        if self._child is not None and not self._child.closed:
            cleanup_failed = True
        if self._child_stdio is not None and not self._child_stdio.closed:
            cleanup_failed = True
        if self._child_job is not None and not self._child_job.handle.closed:
            cleanup_failed = True
        self._child = None
        self._child_stdio = None
        self._child_job = None
        if cleanup_failed or not self._child_job_zero:
            _fail("diagnostic_cleanup_failed")

    def finish_child_and_reprove(self, child: object) -> None:
        if (
            child is not self._child
            or self._resume_count != 1
            or self._capture is None
            or self._mirror is None
        ):
            _fail("diagnostic_boundary_violation")
        self._cleanup_child()
        if prove_runtime_tree(self._mirror.root, self._profile.sid) != self.dacl_proof:
            _fail("diagnostic_boundary_violation")
        prove_runtime_mirror(self._capture, self._mirror)
        self._finished = True

    def close(self) -> DiagnosticCleanupProof:
        cleanup_failed = False
        if self._control is not None or self._control_job is not None:
            try:
                self._cleanup_control()
            except BaseException:
                cleanup_failed = True
        if self._child is not None or self._child_job is not None:
            try:
                self._cleanup_child()
            except BaseException:
                cleanup_failed = True
        self._all_handles_closed = (
            self._child is None
            and self._child_job is None
            and self._child_stdio is None
            and self._control is None
            and self._control_job is None
            and self._control_stdio is None
        )
        try:
            self._profile.delete()
            if self._profile.sid:
                self._profile_absent = self._profile.is_absent()
            else:
                self._profile_absent = not self._profile.created
        except BaseException:
            cleanup_failed = True
        temporary_path = self._temporary_path
        if self._temporary is not None:
            try:
                self._temporary.cleanup()
            except BaseException:
                cleanup_failed = True
        self._temporary = None
        self._temporary_absent = (
            temporary_path is None or not temporary_path.exists()
        )
        proof = DiagnosticCleanupProof(
            self._child_job_zero,
            self._control_job_zero,
            self._all_handles_closed,
            self._profile_absent,
            self._temporary_absent,
        )
        if cleanup_failed:
            _fail("diagnostic_cleanup_failed")
        return proof


def _normalized_error(error: BaseException) -> RuntimeDiagnosticError:
    if isinstance(error, RuntimeDiagnosticError):
        return error
    if isinstance(error, lpac.LpacProofError):
        code = (
            "diagnostic_cleanup_failed"
            if error.code == "sandbox_cleanup_failed"
            else "diagnostic_boundary_violation"
        )
        return RuntimeDiagnosticError(code)
    if isinstance(error, evidence.RootCauseEvidenceError):
        return RuntimeDiagnosticError("diagnostic_collector_failed")
    return RuntimeDiagnosticError("diagnostic_boundary_violation")


def _execute_diagnostic(
    context: _DiagnosticContext, collector: DiagnosticCollector
) -> NativeDiagnosticResult:
    child: object | None = None
    route = ""
    normal_control_created = False
    classification: CollectorClassification | None = None
    pending_error: BaseException | None = None
    collector_started = False
    collector_stopped = False
    cleanup: DiagnosticCleanupProof | None = None
    try:
        child = context.create_suspended_child()
        route, normal_control_created = context.prove_route_and_control(child)
        context.prove_runtime_access(child)
        context.close_child_security(child)

        # The callback name is fixed by the collector adapter.  For P1 it is
        # deliberately invoked only after successful suspended creation and
        # proof; collection still starts before the child's sole resume.
        collector_started = True
        try:
            collector.start_for_suspended_child(
                application=context.application,
                inventory=context.inventory,
                process_id=getattr(child, "process_id"),
                process_handle=getattr(child, "process"),
            )
        except RuntimeDiagnosticError as error:
            if error.code == "diagnostic_cleanup_failed":
                raise
            raise RuntimeDiagnosticError("diagnostic_collector_failed") from None
        except BaseException:
            raise RuntimeDiagnosticError("diagnostic_collector_failed") from None
        context.resume_once(child)
        context.wait_access_denied(child)
        try:
            classification = collector.stop_and_classify(access_denied=True)
        except RuntimeDiagnosticError as error:
            if error.code == "diagnostic_cleanup_failed":
                raise
            raise RuntimeDiagnosticError("diagnostic_collector_failed") from None
        except BaseException:
            raise RuntimeDiagnosticError("diagnostic_collector_failed") from None
        if (
            type(classification) is not CollectorClassification
            or type(classification.cleanup_proved) is not bool
            or not classification.cleanup_proved
        ):
            _fail("diagnostic_collector_failed")
        collector_stopped = True
        context.finish_child_and_reprove(child)
    except BaseException as error:
        pending_error = error
    finally:
        collector_cleanup_failed = False
        if collector_started and not collector_stopped:
            try:
                collector.abort()
            except BaseException:
                collector_cleanup_failed = True
        try:
            cleanup = context.close()
        except BaseException:
            cleanup = None
        if (
            collector_cleanup_failed
            or cleanup
            != DiagnosticCleanupProof(True, True, True, True, True)
        ):
            pending_error = RuntimeDiagnosticError("diagnostic_cleanup_failed")

    if pending_error is not None:
        raise _normalized_error(pending_error) from None
    if classification is None or cleanup is None:
        _fail("diagnostic_boundary_violation")
    if (
        cleanup
        != DiagnosticCleanupProof(True, True, True, True, True)
        or route not in {lpac.LPAC_PROOF_CLASS_46, lpac.LPAC_PROOF_ACCESS_CHECK}
        or type(normal_control_created) is not bool
    ):
        _fail("diagnostic_cleanup_failed")
    try:
        root_cause = evidence.load_root_cause_evidence(
            classification.document,
            expected_candidate_id=evidence.CURRENT_CANDIDATE_ID,
            expected_runtime_digest=context.runtime_digest,
            subject_proof=evidence.STOCK_CHILD_ACCESS_DENIED_PROOF,
            inventory_manifest=context.inventory.manifest,
            collection_quality=classification.quality,
        )
    except BaseException:
        raise RuntimeDiagnosticError("diagnostic_collector_failed") from None
    return NativeDiagnosticResult(
        context.runtime_digest,
        context.inventory.manifest.manifest_digest,
        context.dacl_proof.proof_digest,
        context.dacl_proof.entry_count,
        route,
        normal_control_created,
        1,
        True,
        root_cause,
        cleanup,
    )


def run_current_runtime_diagnostic(
    collector: DiagnosticCollector,
) -> NativeDiagnosticResult:
    """Run one non-retaining current-runtime diagnosis; never a qualification PASS."""

    context = _RealDiagnosticContext.create()
    return _execute_diagnostic(context, collector)


def run_realtime_current_runtime_diagnostic() -> NativeDiagnosticResult:
    """Concrete native P1 entrypoint using the diskless ETW capability owner."""

    return run_current_runtime_diagnostic(_RealtimeCollectorAdapter())


__all__ = [
    "CollectorClassification",
    "CollectorInventory",
    "CollectorObject",
    "DiagnosticCleanupProof",
    "DiagnosticCollector",
    "NativeDiagnosticResult",
    "RuntimeCapture",
    "RuntimeDiagnosticError",
    "RuntimeEntry",
    "RuntimeMirror",
    "TreeDaclProof",
    "build_collector_inventory",
    "capture_current_cpython_runtime",
    "mirror_cpython_runtime",
    "prove_runtime_mirror",
    "run_current_runtime_diagnostic",
    "run_realtime_current_runtime_diagnostic",
]
