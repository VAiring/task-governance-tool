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
PE_IMPORT_PROVENANCE_DOMAIN = b"taskgov-m241b-pe-import-provenance-v1\0"
API_SET_QUALIFICATION_DOMAIN = b"taskgov-m241b-api-set-qualification-v1\0"
KNOWN_DLL_QUALIFICATION_DOMAIN = b"taskgov-m241b-known-dll-qualification-v1\0"
LOADER_RELATION_DOMAIN = b"taskgov-m241b-loader-relation-v1\0"
RUNTIME_ENTRY_LIMIT = 20_000
RUNTIME_TRAVERSAL_LIMIT = 20_000
RUNTIME_FILE_LIMIT = 32 * 1024 * 1024
RUNTIME_TOTAL_LIMIT = 512 * 1024 * 1024
RUNTIME_INVENTORY_LIMIT = 16 * 1024 * 1024
RUNTIME_COPY_CHUNK = 128 * 1024
RUNTIME_TIMEOUT_SECONDS = 30.0
PE_IMPORT_TIMEOUT_SECONDS = 15.0
PE_IMAGE_LIMIT = 8
PE_SECTION_LIMIT = 96
PE_IMPORT_DESCRIPTOR_LIMIT = 64
PE_IMPORT_RELATION_LIMIT = 256
PE_IMPORT_NAME_LIMIT = 64
API_SET_CONTRACT_LIMIT = 128
API_SET_HOST_WCHAR_LIMIT = 260
API_SET_QUERY_DLL = "api-ms-win-core-apiquery-l2-1-0.dll"
LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
TREE_ENTRY_LIMIT = 100_000
TREE_DESCRIPTOR_LIMIT = 64 * 1024 * 1024
TREE_TIMEOUT_SECONDS = 30.0
CHILD_WAIT_MILLISECONDS = 15_000
STATUS_ACCESS_DENIED = 0xC0000022
FIXED_DIAGNOSTIC_BOOTSTRAP = "raise SystemExit(0)\n"

_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_API_SET_CONTRACT = re.compile(
    r"(?:api|ext)-[a-z0-9]+(?:-[a-z0-9]+)*-l[0-9]+"
    r"-[0-9]+-[0-9]+\.dll\Z"
)
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
class RuntimeImportRelation:
    importer: str = field(repr=False)
    table: str
    dependency: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RuntimeImportProvenance:
    runtime_digest: str
    images: tuple[str, ...] = field(repr=False)
    relations: tuple[RuntimeImportRelation, ...] = field(repr=False)
    provenance_digest: str


@dataclass(frozen=True, slots=True)
class ApiSetHostRelation:
    contract: str = field(repr=False)
    host: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ApiSetQualification:
    provenance_digest: str
    relations: tuple[ApiSetHostRelation, ...] = field(repr=False)
    qualification_digest: str


@dataclass(frozen=True, slots=True)
class KnownDllQualification:
    contracts: tuple[str, ...] = field(repr=False)
    snapshot_digest: str


@dataclass(frozen=True, slots=True)
class LoaderDependencyBinding:
    logical_name: str = field(repr=False)
    origin: str
    host_name: str | None = field(default=None, repr=False)
    host_object_ref: str | None = field(default=None, repr=False)
    provenance_digest: str = ""
    authority_digest: str | None = None
    relation_digest: str = ""


@dataclass(frozen=True, slots=True)
class CollectorObject:
    plane: str
    object_ref: str
    match_kind: str
    component: str = field(repr=False)
    dependency_origin: str | None = field(default=None, repr=False)
    loader_binding: LoaderDependencyBinding | None = field(
        default=None, repr=False
    )


@dataclass(frozen=True, slots=True)
class CollectorInventory:
    runtime_digest: str
    manifest: evidence.InventoryManifest
    objects: tuple[CollectorObject, ...] = field(repr=False)
    api_set_qualification: ApiSetQualification = field(repr=False)
    known_dll_qualification: KnownDllQualification = field(repr=False)

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
        thread_handle: lpac.OwnedHandle,
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


def _image_basename_identity(value: str) -> str | None:
    name = _logical_dependency_identity(value)
    if name is not None:
        return name
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or "\0" in value
        or "/" in value
        or "\\" in value
        or ":" in value
        or _COMPONENT.fullmatch(value) is None
        or not value.casefold().endswith(".exe")
    ):
        return None
    return value.casefold()


def _api_set_contract_identity(value: str) -> str | None:
    name = _logical_dependency_identity(value)
    if name is None or _API_SET_CONTRACT.fullmatch(name) is None:
        return None
    return name


def _top_level_runtime_images(capture: RuntimeCapture) -> tuple[str, ...]:
    if type(capture) is not RuntimeCapture:
        _fail("diagnostic_boundary_violation")
    images: list[str] = []
    folded: set[str] = set()
    for entry in capture.entries:
        if "/" in entry.path or Path(entry.path).suffix.casefold() not in {
            ".exe",
            ".dll",
        }:
            continue
        name = _image_basename_identity(entry.path)
        if name is None or name != entry.path or name in folded:
            _fail("diagnostic_unavailable")
        folded.add(name)
        images.append(name)
    result = tuple(sorted(images))
    if (
        not 1 <= len(result) <= PE_IMAGE_LIMIT
        or "python.exe" not in result
        or not any(
            name.startswith("python") and name.endswith(".dll")
            for name in result
        )
    ):
        _fail("diagnostic_unavailable")
    return result


def _bind_runtime_import_provenance(
    capture: RuntimeCapture,
    relations: tuple[RuntimeImportRelation, ...],
) -> RuntimeImportProvenance:
    images = _top_level_runtime_images(capture)
    if type(relations) is not tuple:
        _fail("diagnostic_boundary_violation")
    canonical_relations: list[RuntimeImportRelation] = []
    observed: set[tuple[str, str, str]] = set()
    for relation in relations:
        if type(relation) is not RuntimeImportRelation:
            _fail("diagnostic_boundary_violation")
        importer = _image_basename_identity(relation.importer)
        dependency = _logical_dependency_identity(relation.dependency)
        key = (importer or "", relation.table, dependency or "")
        if (
            importer is None
            or importer != relation.importer
            or importer not in images
            or relation.table not in {"normal", "delay"}
            or dependency is None
            or dependency != relation.dependency
            or key in observed
        ):
            _fail("diagnostic_unavailable")
        observed.add(key)
        canonical_relations.append(
            RuntimeImportRelation(importer, relation.table, dependency)
        )
    canonical_relations.sort(
        key=lambda item: (
            item.importer.encode("ascii", "strict"),
            item.table,
            item.dependency.encode("ascii", "strict"),
        )
    )
    if not 1 <= len(canonical_relations) <= PE_IMPORT_RELATION_LIMIT:
        _fail("diagnostic_unavailable")
    canonical = {
        "format_version": 1,
        "runtime_digest": capture.runtime_digest,
        "images": list(images),
        "relations": [
            {
                "importer": item.importer,
                "table": item.table,
                "dependency": item.dependency,
            }
            for item in canonical_relations
        ],
    }
    return RuntimeImportProvenance(
        capture.runtime_digest,
        images,
        tuple(canonical_relations),
        _domain_digest(PE_IMPORT_PROVENANCE_DOMAIN, canonical),
    )


def _validated_runtime_import_provenance(
    capture: RuntimeCapture, provenance: RuntimeImportProvenance
) -> RuntimeImportProvenance:
    if type(provenance) is not RuntimeImportProvenance:
        _fail("diagnostic_boundary_violation")
    expected = _bind_runtime_import_provenance(capture, provenance.relations)
    if expected != provenance:
        _fail("diagnostic_boundary_violation")
    return expected


def _bind_api_set_qualification(
    provenance: RuntimeImportProvenance,
    relations: tuple[ApiSetHostRelation, ...],
) -> ApiSetQualification:
    if (
        type(provenance) is not RuntimeImportProvenance
        or _DIGEST.fullmatch(provenance.provenance_digest) is None
        or type(relations) is not tuple
    ):
        _fail("diagnostic_boundary_violation")
    required = tuple(
        sorted(
            {
                relation.dependency
                for relation in provenance.relations
                if _api_set_contract_identity(relation.dependency) is not None
            }
        )
    )
    canonical_relations: list[ApiSetHostRelation] = []
    observed: set[str] = set()
    for relation in relations:
        if type(relation) is not ApiSetHostRelation:
            _fail("diagnostic_boundary_violation")
        contract = _api_set_contract_identity(relation.contract)
        host = _logical_dependency_identity(relation.host)
        if (
            contract is None
            or contract != relation.contract
            or host is None
            or host != relation.host
            or host == contract
            or _api_set_contract_identity(host) is not None
            or contract in observed
        ):
            _fail("diagnostic_unavailable")
        observed.add(contract)
        canonical_relations.append(ApiSetHostRelation(contract, host))
    canonical_relations.sort(key=lambda item: item.contract)
    if (
        tuple(item.contract for item in canonical_relations) != required
        or len(canonical_relations) > API_SET_CONTRACT_LIMIT
    ):
        _fail("diagnostic_unavailable")
    canonical = {
        "format_version": 1,
        "provenance_digest": provenance.provenance_digest,
        "relations": [
            {"contract": item.contract, "host": item.host}
            for item in canonical_relations
        ],
    }
    return ApiSetQualification(
        provenance.provenance_digest,
        tuple(canonical_relations),
        _domain_digest(API_SET_QUALIFICATION_DOMAIN, canonical),
    )


def _validated_api_set_qualification(
    provenance: RuntimeImportProvenance,
    qualification: ApiSetQualification,
) -> ApiSetQualification:
    if type(qualification) is not ApiSetQualification:
        _fail("diagnostic_boundary_violation")
    expected = _bind_api_set_qualification(provenance, qualification.relations)
    if expected != qualification:
        _fail("diagnostic_boundary_violation")
    return expected


def _bind_known_dll_qualification(
    system_images: tuple[str, ...],
    snapshot_metadata: tuple[int, int, int],
    snapshot_rows: tuple[tuple[str, str, int], ...],
) -> KnownDllQualification:
    if (
        type(system_images) is not tuple
        or type(snapshot_metadata) is not tuple
        or len(snapshot_metadata) != 3
        or any(type(value) is not int for value in snapshot_metadata)
        or snapshot_metadata[0] < 0
        or not 1 <= snapshot_metadata[1] <= _KNOWN_DLL_VALUE_LIMIT
        or snapshot_metadata[2] < 0
        or type(snapshot_rows) is not tuple
        or snapshot_metadata[1] != len(snapshot_rows)
        or tuple(sorted(system_images)) != system_images
        or len(set(system_images)) != len(system_images)
        or any(_logical_dependency_identity(name) != name for name in system_images)
        or tuple(sorted(snapshot_rows)) != snapshot_rows
        or any(
            type(row) is not tuple
            or len(row) != 3
            or type(row[0]) is not str
            or not row[0]
            or "\0" in row[0]
            or len(row[0]) > 1_024
            or type(row[1]) is not str
            or "\0" in row[1]
            or len(row[1]) > 1_024
            or type(row[2]) is not int
            or row[2] not in {1, 2}
            for row in snapshot_rows
        )
    ):
        _fail("diagnostic_boundary_violation")
    names: set[str] = set()
    dll_data: set[str] = set()
    contracts: list[str] = []
    available = set(system_images)
    total_text = 0
    for value_name, value_data, value_kind in snapshot_rows:
        canonical_name = value_name.casefold()
        if canonical_name in names:
            _fail("diagnostic_unavailable")
        names.add(canonical_name)
        total_text += len(value_name) + len(value_data)
        if total_text > _KNOWN_DLL_TEXT_LIMIT:
            _fail("diagnostic_unavailable")
        if value_kind != 1:
            continue
        dependency = _logical_dependency_identity(value_data)
        if dependency is None:
            continue
        if dependency in dll_data:
            _fail("diagnostic_unavailable")
        dll_data.add(dependency)
        if dependency in available:
            contracts.append(dependency)
    canonical_contracts = tuple(sorted(contracts))
    if not {"kernel32.dll", "ntdll.dll"}.issubset(canonical_contracts):
        _fail("diagnostic_unavailable")
    canonical = {
        "format_version": 1,
        "contracts": list(canonical_contracts),
        "snapshot_metadata": list(snapshot_metadata),
        "snapshot_rows": [list(row) for row in snapshot_rows],
    }
    return KnownDllQualification(
        canonical_contracts,
        _domain_digest(KNOWN_DLL_QUALIFICATION_DOMAIN, canonical),
    )


def _validated_known_dll_qualification(
    system_images: tuple[str, ...], qualification: KnownDllQualification
) -> KnownDllQualification:
    if (
        type(qualification) is not KnownDllQualification
        or type(qualification.contracts) is not tuple
        or not qualification.contracts
        or tuple(sorted(qualification.contracts)) != qualification.contracts
        or len(set(qualification.contracts)) != len(qualification.contracts)
        or any(
            _logical_dependency_identity(name) != name
            for name in qualification.contracts
        )
        or not {"kernel32.dll", "ntdll.dll"}.issubset(
            qualification.contracts
        )
        or not set(qualification.contracts).issubset(system_images)
        or _DIGEST.fullmatch(qualification.snapshot_digest) is None
    ):
        _fail("diagnostic_boundary_violation")
    return qualification


def _bind_loader_dependency(
    *,
    logical_name: str,
    origin: str,
    host_name: str | None,
    host_object_ref: str | None,
    provenance_digest: str,
    authority_digest: str | None,
) -> LoaderDependencyBinding:
    name = _logical_dependency_identity(logical_name)
    host = (
        _logical_dependency_identity(host_name)
        if host_name is not None
        else None
    )
    if (
        name is None
        or name != logical_name
        or _DIGEST.fullmatch(provenance_digest) is None
        or origin not in {"pe_import", "known_dll", "api_set"}
    ):
        _fail("diagnostic_boundary_violation")
    if origin == "pe_import":
        if (
            _api_set_contract_identity(name) is not None
            or host is not None
            or host_object_ref is not None
            or authority_digest is not None
        ):
            _fail("diagnostic_boundary_violation")
    elif origin == "known_dll":
        if (
            _api_set_contract_identity(name) is not None
            or host != name
            or authority_digest is None
        ):
            _fail("diagnostic_boundary_violation")
    else:
        if (
            _api_set_contract_identity(name) != name
            or host is None
            or host == name
            or _api_set_contract_identity(host) is not None
            or authority_digest is None
        ):
            _fail("diagnostic_boundary_violation")
    if (
        origin in {"known_dll", "api_set"}
        and (
            type(host_object_ref) is not str
            or re.fullmatch(r"inventory-sha256:[0-9a-f]{64}", host_object_ref)
            is None
        )
    ):
        _fail("diagnostic_boundary_violation")
    if authority_digest is not None and _DIGEST.fullmatch(authority_digest) is None:
        _fail("diagnostic_boundary_violation")
    canonical = {
        "format_version": 1,
        "logical_name": name,
        "origin": origin,
        "host_name": host,
        "host_object_ref": host_object_ref,
        "provenance_digest": provenance_digest,
        "authority_digest": authority_digest,
    }
    return LoaderDependencyBinding(
        name,
        origin,
        host,
        host_object_ref,
        provenance_digest,
        authority_digest,
        _domain_digest(LOADER_RELATION_DOMAIN, canonical),
    )


def _logical_dependency_component(value: str) -> str | None:
    prefix = "logical-dependency:"
    if type(value) is not str or not value.startswith(prefix):
        return None
    suffix = value.removeprefix(prefix)
    return "sha256:" + suffix if re.fullmatch(r"[0-9a-f]{64}", suffix) else None


def _loader_component(binding: LoaderDependencyBinding) -> str:
    if type(binding) is not LoaderDependencyBinding:
        _fail("diagnostic_boundary_violation")
    expected = _bind_loader_dependency(
        logical_name=binding.logical_name,
        origin=binding.origin,
        host_name=binding.host_name,
        host_object_ref=binding.host_object_ref,
        provenance_digest=binding.provenance_digest,
        authority_digest=binding.authority_digest,
    )
    if expected != binding:
        _fail("diagnostic_boundary_violation")
    return "logical-dependency:" + binding.relation_digest.removeprefix("sha256:")


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


class _InventoryAuthorityLease:
    """Sealed owner for window-stable loader authority snapshots."""

    def initialize(self, inventory: CollectorInventory) -> None:
        raise NotImplementedError

    def reprove(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class _HeldInventoryAuthorityLease(_InventoryAuthorityLease):
    def __init__(self) -> None:
        self._inventory: CollectorInventory | None = None
        self._api: _ApiSetApiLease | None = None
        self._known: _KnownDllRegistryLease | None = None

    def initialize(self, inventory: CollectorInventory) -> None:
        if self._inventory is not None or type(inventory) is not CollectorInventory:
            _fail("diagnostic_collector_failed")
        self._inventory = inventory
        try:
            system_images = tuple(
                sorted(
                    item.component.removeprefix("system32-image:")
                    for item in inventory.objects
                    if item.plane == "dll_image_load"
                    and item.match_kind == "exact_image_identity"
                    and item.component.startswith("system32-image:")
                )
            )
            if inventory.api_set_qualification.relations:
                self._api = _ApiSetApiLease()
                self._api.open()
            self._known = _KnownDllRegistryLease()
            self._known.open(system_images)
            self.reprove()
        except BaseException as error:
            raise _normalized_error(error) from None

    def reprove(self) -> None:
        inventory = self._inventory
        if inventory is None:
            _fail("diagnostic_collector_failed")
        try:
            if inventory.api_set_qualification.relations:
                if self._api is None:
                    _fail("diagnostic_collector_failed")
                _prove_api_set_qualification_with_api(
                    inventory.api_set_qualification, self._api
                )
            if self._known is None:
                _fail("diagnostic_collector_failed")
            observed = self._known.qualification()
            if observed != inventory.known_dll_qualification:
                _fail("diagnostic_collector_failed")
        except RuntimeDiagnosticError as error:
            if error.code == "diagnostic_cleanup_failed":
                raise
            _fail("diagnostic_collector_failed")
        except BaseException:
            _fail("diagnostic_collector_failed")

    def close(self) -> None:
        cleanup_failed = False
        if self._api is not None:
            try:
                self._api.close()
                self._api = None
            except BaseException:
                cleanup_failed = True
        if self._known is not None:
            try:
                self._known.close()
                self._known = None
            except BaseException:
                cleanup_failed = True
        if self._api is None and self._known is None:
            self._inventory = None
        if cleanup_failed:
            _fail("diagnostic_cleanup_failed")


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
        self._authority: _InventoryAuthorityLease | None = None
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
        authority_lease_factory: Callable[
            [], _InventoryAuthorityLease
        ],
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
            or not callable(authority_lease_factory)
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
            authority = authority_lease_factory()
            if not isinstance(authority, _InventoryAuthorityLease):
                _fail("diagnostic_collector_failed")
            self._authority = authority
            authority.initialize(inventory)
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
                    image_origin = "system32"
                elif component.startswith("logical-dependency:"):
                    dependency = item.loader_binding
                    if (
                        dependency is None
                        or _logical_dependency_component(component)
                        != dependency.relation_digest
                        or _loader_component(dependency) != component
                        or item.dependency_origin != dependency.origin
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
                    or item.loader_binding is not None
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
                dict[
                    str,
                    dict[tuple[int, bytes], tuple[_AliasLease, str]],
                ],
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
                    if image_name is not None and plane == "dll_image_load":
                        assert image_origin is not None
                        images_by_name.setdefault(image_name, {}).setdefault(
                            image_origin, {}
                        )[lease.file_identity] = (lease, object_ref)

            for item in dependencies:
                dependency = item.loader_binding
                name = dependency.logical_name if dependency is not None else None
                if (
                    item.plane != "dll_image_load"
                    or name is None
                    or name in self._loader_dependencies
                    or item.match_kind
                    != {
                        "pe_import": "exact_static_pe_import",
                        "known_dll": "exact_known_dll_import",
                        "api_set": "exact_api_set_import",
                    }.get(dependency.origin)
                ):
                    _fail("diagnostic_collector_failed")
                if dependency.origin == "pe_import":
                    if dependency.host_name is not None:
                        _fail("diagnostic_collector_failed")
                elif dependency.origin in {"known_dll", "api_set"}:
                    host = dependency.host_name
                    if host is None:
                        _fail("diagnostic_collector_failed")
                    observed_origins = images_by_name.get(host, {})
                    if (
                        set(observed_origins) != {"system32"}
                        or len(observed_origins["system32"]) != 1
                    ):
                        _fail("diagnostic_collector_failed")
                    (_held, observed_ref), = observed_origins[
                        "system32"
                    ].values()
                    if observed_ref != dependency.host_object_ref:
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
        if self._authority is None:
            _fail("diagnostic_collector_failed")
        try:
            self._authority.reprove()
        except RuntimeDiagnosticError as error:
            if error.code == "diagnostic_cleanup_failed":
                raise
            failed = True
        except BaseException:
            failed = True
        for lease in self._leases:
            try:
                lease.reprove()
            except BaseException:
                failed = True
        if failed:
            _fail("diagnostic_collector_failed")
        self._stable_proved = True

    def close(self) -> None:
        if self._closed and not self._leases and self._authority is None:
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
        if self._authority is not None:
            try:
                self._authority.close()
                self._authority = None
            except BaseException:
                cleanup_failed = True
        self._closed = not self._leases and self._authority is None
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
        authority_lease_factory: Callable[
            [], _InventoryAuthorityLease
        ]
        | None = None,
        ordinal_equal: Callable[[str, str], bool] | None = None,
    ) -> None:
        if not callable(collector_factory):
            _fail("diagnostic_collector_failed")
        self._factory = collector_factory
        self._system32_root = system32_root
        self._current_user_sid = current_user_sid
        self._alias_lease_factory = alias_lease_factory
        self._authority_lease_factory = authority_lease_factory
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
        thread_handle: lpac.OwnedHandle,
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
            or not isinstance(thread_handle, lpac.OwnedHandle)
            or thread_handle.closed
        ):
            _fail("diagnostic_collector_failed")
        system32_root = self._system32_root or _system32_root()
        current_user_sid = self._current_user_sid or lpac._current_user_sid()
        alias_lease_factory = (
            self._alias_lease_factory or _open_trusted_alias_lease
        )
        ordinal_equal = self._ordinal_equal or _windows_ordinal_equal
        authority_lease_factory = (
            self._authority_lease_factory
            or _HeldInventoryAuthorityLease
        )
        resolver = _ExactInventoryResolver()
        self._resolver = resolver
        resolver.initialize(
            application=application,
            inventory=inventory,
            system32_root=system32_root,
            current_user_sid=current_user_sid,
            alias_lease_factory=alias_lease_factory,
            authority_lease_factory=authority_lease_factory,
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
                    and item.loader_binding is not None
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
                thread_handle=thread_handle.value,
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


def _read_mirror_entry_bytes(
    entry: RuntimeEntry, *, deadline: float
) -> bytes:
    """Read, hash, and reprove one exact mirrored file through one descriptor."""

    descriptor: int | None = None
    try:
        if (
            type(entry) is not RuntimeEntry
            or time.monotonic() > deadline
            or not 0 < entry.size_bytes <= RUNTIME_FILE_LIMIT
            or re.fullmatch(r"[0-9a-f]{64}", entry.sha256) is None
        ):
            _fail("diagnostic_unavailable")
        before = entry.source_path.lstat()
        if (
            _is_reparse(entry.source_path)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size != entry.size_bytes
        ):
            _fail("diagnostic_unavailable")
        descriptor = os.open(
            entry.source_path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if not _same_file(before, opened) or opened.st_size != entry.size_bytes:
            _fail("diagnostic_unavailable")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        while total < entry.size_bytes:
            if time.monotonic() > deadline:
                _fail("diagnostic_unavailable")
            chunk = os.read(
                descriptor,
                min(RUNTIME_COPY_CHUNK, entry.size_bytes - total),
            )
            if not chunk:
                _fail("diagnostic_unavailable")
            total += len(chunk)
            if total > entry.size_bytes:
                _fail("diagnostic_unavailable")
            digest.update(chunk)
            chunks.append(chunk)
        if os.read(descriptor, 1):
            _fail("diagnostic_unavailable")
        after = os.fstat(descriptor)
        path_after = entry.source_path.lstat()
        if (
            total != entry.size_bytes
            or digest.hexdigest() != entry.sha256
            or not _same_file(opened, after)
            or not _same_file(before, path_after)
            or after.st_size != entry.size_bytes
            or path_after.st_size != entry.size_bytes
            or _is_reparse(entry.source_path)
        ):
            _fail("diagnostic_unavailable")
        return b"".join(chunks)
    except RuntimeDiagnosticError:
        raise
    except (MemoryError, OSError, RuntimeError):
        _fail("diagnostic_unavailable")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                _fail("diagnostic_cleanup_failed")


@dataclass(frozen=True, slots=True)
class _PeSection:
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int
    characteristics: int


class _PeImportReader:
    """Strict closed PE32+/AMD64 import reader over already-verified bytes."""

    def __init__(
        self, body: bytes, *, image_name: str, deadline: float
    ) -> None:
        if (
            type(body) is not bytes
            or not body
            or len(body) > RUNTIME_FILE_LIMIT
            or _image_basename_identity(image_name) != image_name
            or type(deadline) is not float
        ):
            _fail("diagnostic_unavailable")
        self._body = body
        self._image_name = image_name
        self._deadline = deadline
        self._sections: tuple[_PeSection, ...] = ()
        self._size_of_headers = 0

    def _check(self) -> None:
        if time.monotonic() > self._deadline:
            _fail("diagnostic_unavailable")

    def _slice(self, offset: int, size: int) -> bytes:
        self._check()
        if (
            type(offset) is not int
            or type(size) is not int
            or offset < 0
            or size < 0
            or offset > len(self._body) - size
        ):
            _fail("diagnostic_unavailable")
        return self._body[offset : offset + size]

    def _u16(self, offset: int) -> int:
        return int.from_bytes(self._slice(offset, 2), "little")

    def _u32(self, offset: int) -> int:
        return int.from_bytes(self._slice(offset, 4), "little")

    def _u64(self, offset: int) -> int:
        return int.from_bytes(self._slice(offset, 8), "little")

    @staticmethod
    def _power_of_two(value: int) -> bool:
        return value > 0 and value & (value - 1) == 0

    def _initialize_headers(self) -> tuple[tuple[int, int], tuple[int, int]]:
        if len(self._body) < 64 or self._slice(0, 2) != b"MZ":
            _fail("diagnostic_unavailable")
        pe_offset = self._u32(0x3C)
        if pe_offset < 64 or pe_offset > len(self._body) - 264:
            _fail("diagnostic_unavailable")
        if self._slice(pe_offset, 4) != b"PE\0\0":
            _fail("diagnostic_unavailable")
        coff = pe_offset + 4
        if self._u16(coff) != 0x8664:
            _fail("diagnostic_unavailable")
        section_count = self._u16(coff + 2)
        optional_size = self._u16(coff + 16)
        characteristics = self._u16(coff + 18)
        if (
            not 1 <= section_count <= PE_SECTION_LIMIT
            or optional_size < 128
            or optional_size > 0xF0
            or characteristics & 0x0002 == 0
            or (
                self._image_name.endswith(".dll")
                and characteristics & 0x2000 == 0
            )
            or (
                self._image_name.endswith(".exe")
                and characteristics & 0x2000 != 0
            )
        ):
            _fail("diagnostic_unavailable")
        optional = coff + 20
        section_table = optional + optional_size
        section_table_end = section_table + section_count * 40
        directory_count = self._u32(optional + 108)
        if (
            self._u16(optional) != 0x20B
            or not 14 <= directory_count <= 16
            or optional_size != 112 + directory_count * 8
            or section_table_end > len(self._body)
        ):
            _fail("diagnostic_unavailable")
        section_alignment = self._u32(optional + 32)
        file_alignment = self._u32(optional + 36)
        size_of_image = self._u32(optional + 56)
        size_of_headers = self._u32(optional + 60)
        if (
            not self._power_of_two(file_alignment)
            or not 512 <= file_alignment <= 65_536
            or not self._power_of_two(section_alignment)
            or section_alignment < file_alignment
            or (
                section_alignment < 4_096
                and section_alignment != file_alignment
            )
            or size_of_image == 0
            or size_of_image % section_alignment != 0
            or size_of_headers < section_table_end
            or size_of_headers > len(self._body)
            or size_of_headers % file_alignment != 0
        ):
            _fail("diagnostic_unavailable")
        sections: list[_PeSection] = []
        virtual_ranges: list[tuple[int, int]] = []
        raw_ranges: list[tuple[int, int]] = []
        for index in range(section_count):
            base = section_table + index * 40
            virtual_size = self._u32(base + 8)
            virtual_address = self._u32(base + 12)
            raw_size = self._u32(base + 16)
            raw_offset = self._u32(base + 20)
            section_characteristics = self._u32(base + 36)
            if (
                section_characteristics & 0x20000000
                and section_characteristics & 0x80000000
            ):
                _fail("diagnostic_unavailable")
            mapped_size = max(virtual_size, raw_size)
            if (
                virtual_address == 0
                or virtual_address < size_of_headers
                or virtual_address % section_alignment != 0
                or mapped_size == 0
                or virtual_address > size_of_image - mapped_size
            ):
                _fail("diagnostic_unavailable")
            virtual_ranges.append(
                (virtual_address, virtual_address + mapped_size)
            )
            if raw_size:
                if (
                    raw_offset < size_of_headers
                    or raw_offset % file_alignment != 0
                    or raw_size % file_alignment != 0
                    or raw_offset > len(self._body) - raw_size
                ):
                    _fail("diagnostic_unavailable")
                raw_ranges.append((raw_offset, raw_offset + raw_size))
            elif raw_offset != 0:
                _fail("diagnostic_unavailable")
            sections.append(
                _PeSection(
                    virtual_address,
                    virtual_size,
                    raw_offset,
                    raw_size,
                    section_characteristics,
                )
            )
        if virtual_ranges != sorted(virtual_ranges) or raw_ranges != sorted(
            raw_ranges
        ):
            _fail("diagnostic_unavailable")
        for ranges in (virtual_ranges, raw_ranges):
            ordered = sorted(ranges)
            if any(
                current[0] < previous[1]
                for previous, current in zip(ordered, ordered[1:])
            ):
                _fail("diagnostic_unavailable")
        self._sections = tuple(sections)
        self._size_of_headers = size_of_headers
        normal = (self._u32(optional + 120), self._u32(optional + 124))
        delay = (
            (self._u32(optional + 216), self._u32(optional + 220))
            if directory_count > 13
            else (0, 0)
        )
        return normal, delay

    def _rva_window(
        self, rva: int, *, require_readonly: bool = False
    ) -> tuple[int, int]:
        self._check()
        if type(rva) is not int or rva <= 0:
            _fail("diagnostic_unavailable")
        matches: list[tuple[int, int]] = []
        for section in self._sections:
            if (
                section.raw_size
                and section.virtual_address
                <= rva
                < section.virtual_address + section.raw_size
            ):
                if (
                    section.characteristics & 0x40000000 == 0
                    or (
                        require_readonly
                        and section.characteristics & 0x80000000 != 0
                    )
                ):
                    _fail("diagnostic_unavailable")
                delta = rva - section.virtual_address
                matches.append(
                    (
                        section.raw_offset + delta,
                        section.raw_size - delta,
                    )
                )
        if len(matches) != 1:
            _fail("diagnostic_unavailable")
        return matches[0]

    def _rva_slice(
        self, rva: int, size: int, *, require_readonly: bool = False
    ) -> bytes:
        offset, available = self._rva_window(
            rva, require_readonly=require_readonly
        )
        if size < 0 or size > available:
            _fail("diagnostic_unavailable")
        return self._slice(offset, size)

    def _ascii_z(
        self, rva: int, *, limit: int, require_readonly: bool = False
    ) -> str:
        offset, available = self._rva_window(
            rva, require_readonly=require_readonly
        )
        maximum = min(available, limit + 1)
        raw = self._slice(offset, maximum)
        terminator = raw.find(b"\0")
        if terminator <= 0 or terminator > limit:
            _fail("diagnostic_unavailable")
        value = raw[:terminator]
        if any(byte < 0x21 or byte > 0x7E for byte in value):
            _fail("diagnostic_unavailable")
        try:
            return value.decode("ascii", "strict")
        except UnicodeError:
            _fail("diagnostic_unavailable")

    def _descriptor_rows(
        self, *, rva: int, size: int, width: int
    ) -> tuple[bytes, ...]:
        if rva == 0 and size == 0:
            return ()
        if (
            rva == 0
            or size == 0
            or size % width != 0
            or size // width > PE_IMPORT_DESCRIPTOR_LIMIT
        ):
            _fail("diagnostic_unavailable")
        body = self._rva_slice(rva, size)
        rows: list[bytes] = []
        for index in range(size // width):
            row = body[index * width : (index + 1) * width]
            if row == b"\0" * width:
                if any(body[(index + 1) * width :]):
                    _fail("diagnostic_unavailable")
                return tuple(rows)
            rows.append(row)
        _fail("diagnostic_unavailable")

    @staticmethod
    def _row_u32(row: bytes, index: int) -> int:
        return int.from_bytes(row[index * 4 : index * 4 + 4], "little")

    def _normal_imports(self, rva: int, size: int) -> tuple[str, ...]:
        names: list[str] = []
        observed: set[str] = set()
        for row in self._descriptor_rows(rva=rva, size=size, width=20):
            original = self._row_u32(row, 0)
            name_rva = self._row_u32(row, 3)
            iat = self._row_u32(row, 4)
            if name_rva == 0 or iat == 0:
                _fail("diagnostic_unavailable")
            self._rva_slice(original or iat, 8)
            self._rva_slice(iat, 8)
            name = _logical_dependency_identity(
                self._ascii_z(name_rva, limit=128)
            )
            if name is None or name in observed:
                _fail("diagnostic_unavailable")
            observed.add(name)
            names.append(name)
        return tuple(names)

    def _delay_imports(self, rva: int, size: int) -> tuple[str, ...]:
        names: list[str] = []
        observed: set[str] = set()
        for row in self._descriptor_rows(rva=rva, size=size, width=32):
            attributes = self._row_u32(row, 0)
            name_rva = self._row_u32(row, 1)
            module_handle = self._row_u32(row, 2)
            iat = self._row_u32(row, 3)
            import_names = self._row_u32(row, 4)
            bound_iat = self._row_u32(row, 5)
            unload_iat = self._row_u32(row, 6)
            # Current MSVC delayimp uses the sole dlattrRva bit.  Optional
            # bound/unload RVAs are admitted only when
            # they remain inside one exact raw-backed readable section.
            if (
                attributes != 1
                or name_rva == 0
                or module_handle == 0
                or iat == 0
                or import_names == 0
            ):
                _fail("diagnostic_unavailable")
            self._rva_slice(module_handle, 8)
            self._rva_slice(iat, 8)
            self._rva_slice(import_names, 8)
            if bound_iat:
                self._rva_slice(bound_iat, 8)
            if unload_iat:
                self._rva_slice(unload_iat, 8)
            name = _logical_dependency_identity(
                self._ascii_z(
                    name_rva, limit=128, require_readonly=True
                )
            )
            if name is None or name in observed:
                _fail("diagnostic_unavailable")
            observed.add(name)
            names.append(name)
        return tuple(names)

    def imports(self) -> tuple[tuple[str, str], ...]:
        normal, delay = self._initialize_headers()
        result = [
            *(('normal', name) for name in self._normal_imports(*normal)),
            *(('delay', name) for name in self._delay_imports(*delay)),
        ]
        return tuple(result)


def extract_runtime_import_provenance(
    capture: RuntimeCapture, mirror: RuntimeMirror
) -> RuntimeImportProvenance:
    if (
        type(capture) is not RuntimeCapture
        or type(mirror) is not RuntimeMirror
        or mirror.runtime_digest != capture.runtime_digest
        or mirror.canonical_size != capture.canonical_size
        or mirror.total_bytes != capture.total_bytes
        or not mirror.root.is_absolute()
        or mirror.executable != mirror.root / "python.exe"
        or tuple(item.path for item in mirror.entries)
        != tuple(item.path for item in capture.entries)
    ):
        _fail("diagnostic_boundary_violation")
    images = _top_level_runtime_images(capture)
    capture_by_path = {item.path: item for item in capture.entries}
    mirror_by_path = {item.path: item for item in mirror.entries}
    if len(capture_by_path) != len(capture.entries) or len(mirror_by_path) != len(
        mirror.entries
    ):
        _fail("diagnostic_boundary_violation")
    deadline = time.monotonic() + PE_IMPORT_TIMEOUT_SECONDS
    relations: list[RuntimeImportRelation] = []
    for image in images:
        captured = capture_by_path.get(image)
        mirrored = mirror_by_path.get(image)
        expected_path = mirror.root / image
        if (
            captured is None
            or mirrored is None
            or mirrored.canonical_value() != captured.canonical_value()
            or mirrored.source_path != expected_path
            or not mirrored.source_path.is_absolute()
        ):
            _fail("diagnostic_boundary_violation")
        body = _read_mirror_entry_bytes(mirrored, deadline=deadline)
        for table, dependency in _PeImportReader(
            body, image_name=image, deadline=deadline
        ).imports():
            relations.append(RuntimeImportRelation(image, table, dependency))
            if len(relations) > PE_IMPORT_RELATION_LIMIT:
                _fail("diagnostic_unavailable")
    if len({item.dependency for item in relations}) > PE_IMPORT_NAME_LIMIT:
        _fail("diagnostic_unavailable")
    return _bind_runtime_import_provenance(capture, tuple(relations))


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


def _build_collector_inventory_from_proofs(
    capture: RuntimeCapture,
    *,
    import_provenance: RuntimeImportProvenance,
    api_set_qualification: ApiSetQualification,
    known_dll_qualification: KnownDllQualification,
    system_images: tuple[str, ...],
    appcontainer_sid: str = "S-1-15-2-1-1",
) -> CollectorInventory:
    """Bind validated logical contracts without claiming physical resolution."""

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
        or type(appcontainer_sid) is not str
        or not appcontainer_sid.startswith("S-1-15-2-")
        or appcontainer_sid == lpac.ALL_APPLICATION_PACKAGES_SID
    ):
        _fail("diagnostic_boundary_violation")
    provenance = _validated_runtime_import_provenance(
        capture, import_provenance
    )
    api_qualification = _validated_api_set_qualification(
        provenance, api_set_qualification
    )
    known_qualification = _validated_known_dll_qualification(
        system_images, known_dll_qualification
    )
    runtime_images = provenance.images
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
    runtime_dll_names = {
        item for item in runtime_images if item.endswith(".dll")
    }
    if runtime_dll_names & set(system_images):
        _fail("diagnostic_unavailable")
    imported_names = tuple(
        sorted({item.dependency for item in provenance.relations})
    )
    api_relations = {
        item.contract: item.host for item in api_qualification.relations
    }
    known_names = set(known_qualification.contracts)
    if set(api_relations) & known_names:
        _fail("diagnostic_unavailable")
    if (
        not set(known_qualification.contracts).issubset(system_images)
        or not set(api_relations.values()).issubset(system_images)
    ):
        _fail("diagnostic_unavailable")
    loader_bindings: list[LoaderDependencyBinding] = []
    for name in imported_names:
        if _api_set_contract_identity(name) is not None:
            host = api_relations.get(name)
            if host is None:
                _fail("diagnostic_unavailable")
            binding = _bind_loader_dependency(
                logical_name=name,
                origin="api_set",
                host_name=host,
                host_object_ref=_object_ref(
                    capture.runtime_digest,
                    "dll_image_load",
                    f"system32-image:{host}",
                ),
                provenance_digest=provenance.provenance_digest,
                authority_digest=api_qualification.qualification_digest,
            )
        elif name in known_names:
            binding = _bind_loader_dependency(
                logical_name=name,
                origin="known_dll",
                host_name=name,
                host_object_ref=_object_ref(
                    capture.runtime_digest,
                    "dll_image_load",
                    f"system32-image:{name}",
                ),
                provenance_digest=provenance.provenance_digest,
                authority_digest=known_qualification.snapshot_digest,
            )
        else:
            binding = _bind_loader_dependency(
                logical_name=name,
                origin="pe_import",
                host_name=None,
                host_object_ref=None,
                provenance_digest=provenance.provenance_digest,
                authority_digest=None,
            )
        loader_bindings.append(binding)
    logical_bindings_by_component = {
        _loader_component(item): item for item in loader_bindings
    }
    if len(logical_bindings_by_component) != len(loader_bindings):
        _fail("diagnostic_unavailable")
    logical_dependency_components = tuple(
        sorted(logical_bindings_by_component)
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
                            {
                                "pe_import": "exact_static_pe_import",
                                "known_dll": "exact_known_dll_import",
                                "api_set": "exact_api_set_import",
                            }[logical_bindings_by_component[component].origin]
                            if component in logical_bindings_by_component
                            else {
                                "file_access": "exact_file_identity",
                                "dll_image_load": "exact_image_identity",
                                "registry_access": "canonical_registry_key",
                                "code_integrity_policy": "exact_ci_image_identity",
                            }[plane]
                        ),
                        component,
                        (
                            logical_bindings_by_component[component].origin
                            if component in logical_bindings_by_component
                            else None
                        ),
                        logical_bindings_by_component.get(component),
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
    return CollectorInventory(
        capture.runtime_digest,
        manifest,
        tuple(objects),
        api_qualification,
        known_qualification,
    )


def build_collector_inventory(
    capture: RuntimeCapture,
    mirror: RuntimeMirror,
    *,
    import_provenance: RuntimeImportProvenance,
    api_set_qualification: ApiSetQualification,
    known_dll_qualification: KnownDllQualification,
    system_images: tuple[str, ...],
    api_authority: _ApiSetApiLease,
    known_dll_authority: _KnownDllRegistryLease,
    appcontainer_sid: str = "S-1-15-2-1-1",
) -> CollectorInventory:
    """Rederive every production proof while its exact owner remains held."""

    observed = extract_runtime_import_provenance(capture, mirror)
    if observed != import_provenance:
        _fail("diagnostic_boundary_violation")
    if (
        type(api_authority) is not _ApiSetApiLease
        or api_authority._module == 0
        or not callable(api_authority.IsApiSetImplemented)
        or not callable(api_authority.GetApiSetModuleBaseName)
        or type(known_dll_authority) is not _KnownDllRegistryLease
        or known_dll_authority._key is None
        or known_dll_authority._winreg is None
        or known_dll_authority._system_images != system_images
    ):
        _fail("diagnostic_boundary_violation")
    observed_api = qualify_api_set_contracts(observed, api=api_authority)
    observed_known = known_dll_authority.qualification()
    if (
        observed_api != api_set_qualification
        or observed_known != known_dll_qualification
    ):
        _fail("diagnostic_boundary_violation")
    return _build_collector_inventory_from_proofs(
        capture,
        import_provenance=observed,
        api_set_qualification=observed_api,
        known_dll_qualification=observed_known,
        system_images=system_images,
        appcontainer_sid=appcontainer_sid,
    )


def _system32_root() -> Path:
    if platform.machine().upper() != "AMD64":
        _fail("diagnostic_unavailable")
    buffer = ctypes.create_unicode_buffer(32_768)
    for index in range(len(buffer)):
        buffer[index] = "\uffff"
    length = int(
        _dacl_apis().kernel32.GetSystemDirectoryW(buffer, len(buffer))
    )
    if (
        length <= 0
        or length >= len(buffer)
        or buffer[length] != "\0"
        or any(buffer[index] == "\0" for index in range(length))
    ):
        _fail("diagnostic_unavailable")
    value = "".join(buffer[index] for index in range(length))
    try:
        wchar_count = len(value.encode("utf-16-le", "strict")) // 2
    except UnicodeError:
        _fail("diagnostic_unavailable")
    if wchar_count != length:
        _fail("diagnostic_unavailable")
    system32 = Path(value)
    if not system32.is_absolute() or not system32.is_dir() or _is_reparse(system32):
        _fail("diagnostic_unavailable")
    return system32


def _verified_system_image_basenames(
    additional_hosts: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Bind only existing non-reparse System32 image basenames."""

    if (
        type(additional_hosts) is not tuple
        or tuple(sorted(additional_hosts)) != additional_hosts
        or len(set(additional_hosts)) != len(additional_hosts)
        or any(
            _logical_dependency_identity(name) != name
            for name in additional_hosts
        )
        or len(additional_hosts) > API_SET_CONTRACT_LIMIT
    ):
        _fail("diagnostic_unavailable")
    system32 = _system32_root()
    verified: list[str] = []
    for name in sorted(set(_SYSTEM_IMAGE_CANDIDATES) | set(additional_hosts)):
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
    if (
        not {"kernel32.dll", "kernelbase.dll", "ntdll.dll"}.issubset(result)
        or not set(additional_hosts).issubset(result)
    ):
        _fail("diagnostic_unavailable")
    return result


class _KnownDllRegistryLease:
    """Own one exact 64-bit KnownDLL key across an observation window."""

    def __init__(self) -> None:
        self._winreg: object | None = None
        self._key: object | None = None
        self._system_images: tuple[str, ...] = ()

    def open(self, system_images: tuple[str, ...]) -> None:
        if self._key is not None or self._winreg is not None:
            _fail("diagnostic_boundary_violation")
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
                or type(winreg.REG_SZ) is not int
                or winreg.REG_SZ != 1
                or type(winreg.REG_EXPAND_SZ) is not int
                or winreg.REG_EXPAND_SZ != 2
                or not callable(winreg.CloseKey)
            ):
                _fail("diagnostic_unavailable")
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs",
                0,
                query_value | wow64_64,
            )
            self._winreg = winreg
            self._key = key
            self._system_images = system_images
        except RuntimeDiagnosticError:
            raise
        except BaseException:
            _fail("diagnostic_unavailable")

    def _snapshot(
        self,
    ) -> tuple[tuple[int, int, int], tuple[tuple[str, str, int], ...]]:
        winreg = self._winreg
        key = self._key
        if winreg is None or key is None:
            _fail("diagnostic_unavailable")
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
            value_name, value_data, value_kind = winreg.EnumValue(key, index)
            if (
                type(value_name) is not str
                or not value_name
                or "\0" in value_name
                or len(value_name) > 1_024
                or type(value_data) is not str
                or "\0" in value_data
                or len(value_data) > 1_024
                or type(value_kind) is not int
                or value_kind not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ}
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

    def qualification(self) -> KnownDllQualification:
        try:
            first_metadata, first = self._snapshot()
            second_metadata, second = self._snapshot()
            if first_metadata != second_metadata or first != second:
                _fail("diagnostic_unavailable")
            return _bind_known_dll_qualification(
                self._system_images, first_metadata, first
            )
        except RuntimeDiagnosticError:
            raise
        except BaseException:
            _fail("diagnostic_unavailable")

    def close(self) -> None:
        if self._key is None:
            self._winreg = None
            self._system_images = ()
            return
        winreg = self._winreg
        if winreg is None:
            _fail("diagnostic_cleanup_failed")
        try:
            winreg.CloseKey(self._key)
        except BaseException:
            _fail("diagnostic_cleanup_failed")
        self._key = None
        self._winreg = None
        self._system_images = ()


def _verified_known_dll_contracts(
    system_images: tuple[str, ...],
) -> KnownDllQualification:
    """One-shot helper; production window owners retain the explicit lease."""

    lease = _KnownDllRegistryLease()
    cleanup_faulted = False
    try:
        lease.open(system_images)
        return lease.qualification()
    finally:
        try:
            lease.close()
        except BaseException:
            cleanup_faulted = True
            try:
                lease.close()
            except BaseException:
                pass
        if cleanup_faulted:
            _fail("diagnostic_cleanup_failed")


_HRESULT_INSUFFICIENT_BUFFER = 0x8007007A


def _hresult_u32(value: object) -> int:
    if type(value) is not int:
        _fail("diagnostic_unavailable")
    return value & 0xFFFFFFFF


class _ApiSetApiLease:
    """Own one fixed apiquery2 HMODULE and its two fixed exports."""

    def __init__(self) -> None:
        self._module = 0
        self.IsApiSetImplemented: object | None = None
        self.GetApiSetModuleBaseName: object | None = None

    def open(self) -> None:
        if self._module != 0:
            _fail("diagnostic_boundary_violation")
        try:
            kernel32 = _dacl_apis().kernel32
            module = int(
                kernel32.LoadLibraryExW(
                    API_SET_QUERY_DLL,
                    None,
                    LOAD_LIBRARY_SEARCH_SYSTEM32,
                )
                or 0
            )
            if module == 0:
                _fail("diagnostic_unavailable")
            self._module = module
            implemented_address = int(
                kernel32.GetProcAddress(
                    ctypes.c_void_p(module), b"IsApiSetImplemented"
                )
                or 0
            )
            base_name_address = int(
                kernel32.GetProcAddress(
                    ctypes.c_void_p(module), b"GetApiSetModuleBaseName"
                )
                or 0
            )
            if implemented_address == 0 or base_name_address == 0:
                _fail("diagnostic_unavailable")
            factory = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
            self.IsApiSetImplemented = factory(
                wintypes.BOOL, ctypes.c_char_p
            )(implemented_address)
            self.GetApiSetModuleBaseName = factory(
                ctypes.c_long,
                ctypes.c_char_p,
                ctypes.c_uint32,
                wintypes.LPWSTR,
                ctypes.POINTER(ctypes.c_uint32),
            )(base_name_address)
        except RuntimeDiagnosticError:
            raise
        except BaseException:
            _fail("diagnostic_unavailable")

    def close(self) -> None:
        if self._module == 0:
            self.IsApiSetImplemented = None
            self.GetApiSetModuleBaseName = None
            return
        if not _dacl_apis().kernel32.FreeLibrary(ctypes.c_void_p(self._module)):
            _fail("diagnostic_cleanup_failed")
        self._module = 0
        self.IsApiSetImplemented = None
        self.GetApiSetModuleBaseName = None


def _query_api_set_snapshot(
    contracts: tuple[str, ...], *, api: object
) -> tuple[ApiSetHostRelation, ...]:
    if (
        type(contracts) is not tuple
        or tuple(sorted(contracts)) != contracts
        or len(set(contracts)) != len(contracts)
        or len(contracts) > API_SET_CONTRACT_LIMIT
        or any(_api_set_contract_identity(item) != item for item in contracts)
    ):
        _fail("diagnostic_unavailable")
    try:
        is_implemented = api.IsApiSetImplemented
        get_base_name = api.GetApiSetModuleBaseName
        if not callable(is_implemented) or not callable(get_base_name):
            _fail("diagnostic_unavailable")
        rows: list[ApiSetHostRelation] = []
        for contract in contracts:
            query = contract.removesuffix(".dll").encode("ascii", "strict")
            if int(is_implemented(query)) != 1:
                _fail("diagnostic_unavailable")
            required = ctypes.c_uint32(0)
            first_result = get_base_name(
                query, 0, None, ctypes.byref(required)
            )
            if (
                _hresult_u32(first_result) != _HRESULT_INSUFFICIENT_BUFFER
                or not 2 <= required.value <= API_SET_HOST_WCHAR_LIMIT
            ):
                _fail("diagnostic_unavailable")
            output = ctypes.create_unicode_buffer(required.value)
            for index in range(required.value):
                output[index] = "\uffff"
            actual = ctypes.c_uint32(0)
            second_result = get_base_name(
                query,
                required.value,
                output,
                ctypes.byref(actual),
            )
            if (
                _hresult_u32(second_result) != 0
                or actual.value != required.value
                or output[required.value - 1] != "\0"
                or any(output[index] == "\0" for index in range(required.value - 1))
            ):
                _fail("diagnostic_unavailable")
            host_raw = "".join(output[index] for index in range(required.value - 1))
            try:
                wchar_count = len(host_raw.encode("utf-16-le", "strict")) // 2
            except UnicodeError:
                _fail("diagnostic_unavailable")
            host = _logical_dependency_identity(host_raw)
            if (
                wchar_count + 1 != required.value
                or host is None
                or host != host_raw
                or host == contract
                or _api_set_contract_identity(host) is not None
            ):
                _fail("diagnostic_unavailable")
            rows.append(ApiSetHostRelation(contract, host))
        return tuple(rows)
    except RuntimeDiagnosticError:
        raise
    except BaseException:
        _fail("diagnostic_unavailable")


def qualify_api_set_contracts(
    provenance: RuntimeImportProvenance, *, api: object
) -> ApiSetQualification:
    if type(provenance) is not RuntimeImportProvenance:
        _fail("diagnostic_boundary_violation")
    contracts = tuple(
        sorted(
            {
                item.dependency
                for item in provenance.relations
                if _api_set_contract_identity(item.dependency) is not None
            }
        )
    )
    first = _query_api_set_snapshot(contracts, api=api)
    second = _query_api_set_snapshot(contracts, api=api)
    if first != second:
        _fail("diagnostic_unavailable")
    return _bind_api_set_qualification(provenance, first)


def _prove_api_set_qualification_with_api(
    qualification: ApiSetQualification, api: object
) -> None:
    if type(qualification) is not ApiSetQualification:
        _fail("diagnostic_collector_failed")
    contracts = tuple(item.contract for item in qualification.relations)
    try:
        first = _query_api_set_snapshot(contracts, api=api)
        second = _query_api_set_snapshot(contracts, api=api)
        canonical = {
            "format_version": 1,
            "provenance_digest": qualification.provenance_digest,
            "relations": [
                {"contract": item.contract, "host": item.host}
                for item in first
            ],
        }
        observed_digest = _domain_digest(
            API_SET_QUALIFICATION_DOMAIN, canonical
        )
    except RuntimeDiagnosticError as error:
        if error.code == "diagnostic_cleanup_failed":
            raise
        _fail("diagnostic_collector_failed")
    except BaseException:
        _fail("diagnostic_collector_failed")
    if (
        first != second
        or first != qualification.relations
        or observed_digest != qualification.qualification_digest
    ):
        _fail("diagnostic_collector_failed")


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
        if (
            os.name != "nt"
            or not hasattr(ctypes, "WinDLL")
            or ctypes.sizeof(HANDLE) != ctypes.sizeof(LPVOID)
            or ctypes.sizeof(DWORD) != 4
        ):
            _fail("diagnostic_unavailable")
        try:
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            k = self.kernel32
            a = self.advapi32
            self._prototype(
                k.GetSystemDirectoryW,
                [wintypes.LPWSTR, DWORD],
                DWORD,
            )
            self._prototype(
                k.LoadLibraryExW,
                [wintypes.LPCWSTR, HANDLE, DWORD],
                HANDLE,
            )
            self._prototype(
                k.GetProcAddress,
                [HANDLE, ctypes.c_char_p],
                LPVOID,
            )
            self._prototype(k.FreeLibrary, [HANDLE], wintypes.BOOL)
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
        self._preflight_api: _ApiSetApiLease | None = None
        self._preflight_known: _KnownDllRegistryLease | None = None
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

    def _close_preflight_authorities(self) -> None:
        cleanup_failed = False
        for attribute in ("_preflight_api", "_preflight_known"):
            owner = getattr(self, attribute)
            if owner is None:
                continue
            try:
                owner.close()
                setattr(self, attribute, None)
            except BaseException:
                cleanup_failed = True
        if cleanup_failed:
            _fail("diagnostic_cleanup_failed")

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
            context.dacl_proof = _seal_exact_runtime_scope(
                context._mirror, context._profile.sid
            )
            prove_runtime_mirror(context._capture, context._mirror)
            provenance = extract_runtime_import_provenance(
                context._capture, context._mirror
            )
            context._preflight_api = _ApiSetApiLease()
            context._preflight_api.open()
            api_qualification = qualify_api_set_contracts(
                provenance, api=context._preflight_api
            )
            api_hosts = tuple(
                sorted({item.host for item in api_qualification.relations})
            )
            system_images = _verified_system_image_basenames(api_hosts)
            context._preflight_known = _KnownDllRegistryLease()
            context._preflight_known.open(system_images)
            known_qualification = context._preflight_known.qualification()
            context.inventory = build_collector_inventory(
                context._capture,
                context._mirror,
                import_provenance=provenance,
                api_set_qualification=api_qualification,
                known_dll_qualification=known_qualification,
                system_images=system_images,
                api_authority=context._preflight_api,
                known_dll_authority=context._preflight_known,
                appcontainer_sid=context._profile.sid,
            )
            context._close_preflight_authorities()
            environment = portability._environment_block(str(scratch))
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
        for attribute in ("_preflight_api", "_preflight_known"):
            owner = getattr(self, attribute)
            if owner is None:
                continue
            closed = False
            close_faulted = False
            for _attempt in range(2):
                try:
                    owner.close()
                    setattr(self, attribute, None)
                    closed = True
                    break
                except BaseException:
                    close_faulted = True
                    continue
            if close_faulted or not closed:
                cleanup_failed = True
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
            and self._preflight_api is None
            and self._preflight_known is None
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
                thread_handle=getattr(child, "thread"),
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
