"""Private, bounded TG-M24.1B real-time ETW qualification collector.

This module is test-only.  It owns neither the LPAC child nor its process
handle.  The caller must create and prove a suspended zero-capability LPAC
child first, then bind this collector before the one permitted resume.  Raw
ETW payloads are translated in the callback and are never returned or kept as
diagnostic records.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import threading
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, NoReturn, Protocol


CURRENT_RUNTIME_CANDIDATE = "current_runtime_unchanged"
SUBJECT_ACCESS_DENIED = "status_access_denied_0xc0000022"
COLLECTION_SCHEMA = "m241b_realtime_collection_v1"
UNKNOWN_COLLECTION_SCHEMA = "unknown"

SESSION_NAME = "OpenAI.TaskGov.M241B.RuntimeQualification"
SESSION_GUID = uuid.UUID("82a6642f-b340-4db5-8d53-09dd78e03262")

EVENT_TRACE_FLAG_PROCESS = 0x00000001
EVENT_TRACE_FLAG_THREAD = 0x00000002
EVENT_TRACE_FLAG_IMAGE_LOAD = 0x00000004
EVENT_TRACE_FLAG_REGISTRY = 0x00020000
EVENT_TRACE_FLAG_FILE_IO = 0x02000000
EVENT_TRACE_FLAG_FILE_IO_INIT = 0x04000000
EVENT_TRACE_FLAG_NO_SYSCONFIG = 0x10000000
EXACT_KERNEL_ENABLE_FLAGS = (
    EVENT_TRACE_FLAG_PROCESS
    | EVENT_TRACE_FLAG_THREAD
    | EVENT_TRACE_FLAG_IMAGE_LOAD
    | EVENT_TRACE_FLAG_REGISTRY
    | EVENT_TRACE_FLAG_FILE_IO
    | EVENT_TRACE_FLAG_FILE_IO_INIT
    | EVENT_TRACE_FLAG_NO_SYSCONFIG
)

EVENT_TRACE_REAL_TIME_MODE = 0x00000100
EVENT_TRACE_SYSTEM_LOGGER_MODE = 0x02000000
EVENT_TRACE_INDEPENDENT_SESSION_MODE = 0x08000000
EXACT_LOG_FILE_MODE = (
    EVENT_TRACE_REAL_TIME_MODE
    | EVENT_TRACE_SYSTEM_LOGGER_MODE
    | EVENT_TRACE_INDEPENDENT_SESSION_MODE
)

MAX_DURATION_SECONDS = 15.0
MAX_CALLBACKS = 100_000
MAX_INSPECTED_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_CHILD_RECORDS = 512
MAX_PENDING_IRPS = 512
MAX_TARGET_THREADS = 128
MAX_TRACKED_THREADS = 16_384
MAX_DEFERRED_FILE_BEGINS = 512
MAX_FILE_OBJECTS = 512
MAX_REGISTRY_HANDLES = 512
MAX_PROPERTY_BYTES = 64 * 1024
MAX_PROVIDER_DESCRIPTOR_BYTES = 1024 * 1024
MAX_PROVIDER_DESCRIPTORS = 4096

PLANE_ORDER = (
    "file_access",
    "dll_image_load",
    "registry_access",
    "code_integrity_policy",
)
_OUTCOMES = frozenset({"denial", "observed_no_denial", "inconclusive"})
_REASONS = frozenset(
    {
        "cleanup_unproved",
        "collection_schema_unproved",
        "observation_ambiguous",
        "observation_overflow",
        "plane_scope_unproved",
        "probe_unavailable",
    }
)
_OPERATIONS = {
    "file_access": frozenset(
        {
            "file_create",
            "file_cleanup",
            "file_close",
            "file_read",
            "file_write",
            "file_set_information",
            "file_delete",
            "file_rename",
            "file_directory_enumeration",
            "file_flush",
            "file_query_information",
            "file_system_control",
            "file_directory_notification",
        }
    ),
    "dll_image_load": frozenset({"image_map"}),
    "registry_access": frozenset(
        {
            "registry_create",
            "registry_open",
            "registry_delete",
            "registry_query",
            "registry_set_value",
            "registry_delete_value",
            "registry_query_value",
            "registry_enumerate_key",
            "registry_enumerate_value",
            "registry_query_multiple_values",
            "registry_set_information",
            "registry_flush",
            "registry_virtualize",
        }
    ),
    "code_integrity_policy": frozenset({"image_policy_validate"}),
}
_DEFAULT_OPERATION = {
    "file_access": "file_create",
    "dll_image_load": "image_map",
    "registry_access": "registry_open",
    "code_integrity_policy": "image_policy_validate",
}
_POLICY = {
    "file_access": "file_io",
    "dll_image_load": "image_loader",
    "registry_access": "registry_access",
    "code_integrity_policy": "code_integrity",
}
_OBJECT_ID = re.compile(r"inventory-sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PROVIDER_BINDING = re.compile(r"provider-sha256:[0-9a-f]{64}\Z")

_STATUS_SUCCESS = 0x00000000
_STATUS_ACCESS_DENIED = 0xC0000022


class RuntimeTraceError(RuntimeError):
    """Closed collector failure which never embeds a rejected native value."""

    _CODES = frozenset(
        {
            "trace_binding_invalid",
            "trace_cleanup_unproved",
            "trace_invalid_state",
            "trace_session_collision",
            "trace_unavailable",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            code = "trace_unavailable"
        self.code = code
        super().__init__(code)


def _fail(code: str) -> NoReturn:
    raise RuntimeTraceError(code) from None


class InventoryResolver(Protocol):
    def __call__(self, plane: str, raw_identity: str) -> str | None: ...


class LoaderDependencyResolver(Protocol):
    def __call__(self, raw_identity: str) -> str | None: ...


class InventoryBinding:
    """Typed allowlist plus a deliberately non-revealing raw identity mapper."""

    __slots__ = (
        "runtime_digest",
        "_objects",
        "_path_objects",
        "_loader_dependency_objects",
        "_path_resolver",
        "_loader_dependency_resolver",
    )

    def __init__(
        self,
        *,
        runtime_digest: str,
        objects_by_plane: Mapping[str, tuple[str, ...]],
        loader_dependency_refs: tuple[str, ...],
        path_resolver: InventoryResolver,
        loader_dependency_resolver: LoaderDependencyResolver,
    ) -> None:
        if (
            type(runtime_digest) is not str
            or _DIGEST.fullmatch(runtime_digest) is None
            or type(objects_by_plane) is not dict
            or tuple(objects_by_plane) != PLANE_ORDER
            or type(loader_dependency_refs) is not tuple
            or not callable(path_resolver)
            or not callable(loader_dependency_resolver)
        ):
            _fail("trace_binding_invalid")
        copied: dict[str, frozenset[str]] = {}
        all_objects: set[str] = set()
        for plane in PLANE_ORDER:
            values = objects_by_plane[plane]
            if (
                type(values) is not tuple
                or not 1 <= len(values) <= 64
                or any(
                    type(value) is not str or _OBJECT_ID.fullmatch(value) is None
                    for value in values
                )
                or len(set(values)) != len(values)
                or all_objects.intersection(values)
            ):
                _fail("trace_binding_invalid")
            copied[plane] = frozenset(values)
            all_objects.update(values)
        if (
            not loader_dependency_refs
            or any(
                type(value) is not str or _OBJECT_ID.fullmatch(value) is None
                for value in loader_dependency_refs
            )
            or len(set(loader_dependency_refs)) != len(loader_dependency_refs)
        ):
            _fail("trace_binding_invalid")
        dependency_objects = frozenset(loader_dependency_refs)
        dll_objects = copied["dll_image_load"]
        if (
            not dependency_objects
            or len(dependency_objects) != len(loader_dependency_refs)
            or not dependency_objects < dll_objects
        ):
            _fail("trace_binding_invalid")
        self.runtime_digest = runtime_digest
        self._objects = MappingProxyType(copied)
        self._loader_dependency_objects = dependency_objects
        self._path_objects = MappingProxyType(
            {
                plane: (
                    copied[plane] - dependency_objects
                    if plane == "dll_image_load"
                    else copied[plane]
                )
                for plane in PLANE_ORDER
            }
        )
        self._path_resolver = path_resolver
        self._loader_dependency_resolver = loader_dependency_resolver

    def contains(self, plane: str, object_ref: str) -> bool:
        return plane in self._objects and object_ref in self._objects[plane]

    def contains_path(self, plane: str, object_ref: str) -> bool:
        return plane in self._path_objects and object_ref in self._path_objects[plane]

    def resolve(self, plane: str, raw_identity: str) -> str | None:
        if plane not in self._objects or type(raw_identity) is not str:
            return None
        try:
            value = self._path_resolver(plane, raw_identity)
        except BaseException:
            return None
        if type(value) is not str or value not in self._path_objects[plane]:
            return None
        return value

    def resolve_loader_dependency(self, raw_identity: str) -> str | None:
        """Resolve only an exact prebound logical loader name in the DLL plane."""

        if type(raw_identity) is not str:
            return None
        try:
            value = self._loader_dependency_resolver(raw_identity)
        except BaseException:
            return None
        if (
            type(value) is not str
            or value not in self._loader_dependency_objects
        ):
            return None
        return value

    def __repr__(self) -> str:
        counts = tuple(len(self._objects[plane]) for plane in PLANE_ORDER)
        return f"InventoryBinding(runtime_digest=<bound>, counts={counts!r})"


@dataclass(frozen=True, slots=True)
class PlaneTraceResult:
    plane: str
    outcome: str
    object_ref: str | None
    operation: str
    policy: str
    reason: str | None

    def __post_init__(self) -> None:
        if (
            self.plane not in PLANE_ORDER
            or self.outcome not in _OUTCOMES
            or self.operation not in _OPERATIONS.get(self.plane, ())
            or self.policy != _POLICY.get(self.plane)
            or (
                self.outcome == "denial"
                and (
                    type(self.object_ref) is not str
                    or _OBJECT_ID.fullmatch(self.object_ref) is None
                    or self.reason is not None
                )
            )
            or (
                self.outcome == "observed_no_denial"
                and (self.object_ref is not None or self.reason is not None)
            )
            or (
                self.outcome == "inconclusive"
                and (
                    self.object_ref is not None
                    or self.reason not in _REASONS
                )
            )
        ):
            _fail("trace_binding_invalid")


@dataclass(frozen=True, slots=True)
class RuntimeTraceResult:
    """Intermediate classification evidence; never a qualification/PASS claim."""

    candidate_id: str
    runtime_digest: str
    subject_proof: str | None
    callbacks: int
    inspected_payload_bytes: int
    child_records: int
    cleanup_proved: bool
    window_binding: str
    quality: tuple["PlaneTraceQuality", ...]
    planes: tuple[PlaneTraceResult, ...]

    @property
    def has_inconclusive(self) -> bool:
        return any(item.outcome == "inconclusive" for item in self.planes)


@dataclass(frozen=True, slots=True)
class StopTraceResult:
    stopped: bool
    events_lost: int
    log_buffers_lost: int
    realtime_buffers_lost: int
    snapshot: "KernelSessionSnapshot | None" = None

    @property
    def loss_observed(self) -> bool:
        return any(
            value > 0
            for value in (
                self.events_lost,
                self.log_buffers_lost,
                self.realtime_buffers_lost,
            )
        )


@dataclass(frozen=True, slots=True)
class KernelSessionSnapshot:
    """One sanitized query result; caller input canaries are never evidence."""

    enable_flags: int
    log_file_mode: int
    client_context: int
    events_lost: int
    log_buffers_lost: int
    realtime_buffers_lost: int

    @property
    def exact(self) -> bool:
        return bool(
            self.enable_flags == EXACT_KERNEL_ENABLE_FLAGS
            and self.log_file_mode == EXACT_LOG_FILE_MODE
            and self.client_context == 1
            and not self.loss_observed
        )

    @property
    def loss_observed(self) -> bool:
        return any(
            type(value) is not int or value != 0
            for value in (
                self.events_lost,
                self.log_buffers_lost,
                self.realtime_buffers_lost,
            )
        )


@dataclass(frozen=True, slots=True)
class PlaneTraceQuality:
    plane: str
    collection_schema: str
    probe_available: bool
    lossless: bool
    overflowed: bool
    plane_scope_complete: bool
    correlation_complete: bool
    cleanup_proved: bool


def _quality_failure_reason(item: PlaneTraceQuality) -> str | None:
    if not item.probe_available:
        return "probe_unavailable"
    if item.collection_schema != COLLECTION_SCHEMA:
        return "collection_schema_unproved"
    if not item.lossless or item.overflowed:
        return "observation_overflow"
    if not item.plane_scope_complete:
        return "plane_scope_unproved"
    if not item.correlation_complete:
        return "observation_ambiguous"
    if not item.cleanup_proved:
        return "cleanup_unproved"
    return None


@dataclass(slots=True)
class _PlaneState:
    successes: int = 0
    operations: set[str] | None = None
    denials: set[tuple[str, str]] | None = None
    reasons: set[str] | None = None

    def __post_init__(self) -> None:
        self.operations = set()
        self.denials = set()
        self.reasons = set()


@dataclass(frozen=True, slots=True)
class _PendingFile:
    object_ref: str | None
    operation: str
    file_object: int
    thread_id: int
    begin_timestamp: int
    object_generation: int | None


@dataclass(frozen=True, slots=True)
class _DeferredFileBegin:
    timestamp: int
    irp: int
    object_ref: str | None
    thread_id: int
    header_pid: int
    file_object: int
    file_key: int
    operation: str
    identity_ambiguous: bool = False
    object_generation: int | None = None
    lifetime_observed: bool = False


@dataclass(frozen=True, slots=True)
class _TimedBinding:
    """One sanitized pointer binding generation in QPC order."""

    object_ref: str | None
    timestamp: int
    generation: int
    last_used_timestamp: int | None = None


@dataclass(frozen=True, slots=True)
class _ThreadEpoch:
    """Latest bounded classic-Thread ownership epoch for one TTID."""

    owner_pid: int
    start_timestamp: int | None
    end_timestamp: int | None = None
    prebound: bool = False
    last_used_timestamp: int | None = None


class _TraceReducer:
    """Raw-free state machine shared by native callbacks and pure fault tests."""

    def __init__(self, inventory: InventoryBinding) -> None:
        self._inventory = inventory
        self._planes = {plane: _PlaneState() for plane in PLANE_ORDER}
        self._pending: dict[int, _PendingFile] = {}
        self._file_objects: dict[int, _TimedBinding] = {}
        self._file_keys: dict[int, _TimedBinding] = {}
        self._registry_handles: dict[int, _TimedBinding] = {}
        self._binding_barriers: dict[str, int | None] = {
            "file_key": None,
            "registry_handle": None,
        }
        self._thread_epochs: dict[int, _ThreadEpoch] = {}
        self._target_threads: set[int] = set()
        self._deferred_file_begins: list[_DeferredFileBegin] = []
        self._last_kernel_timestamp: int | None = None
        self._target_kernel_latest: dict[str, int | None] = {
            "file_access": None,
            "registry_access": None,
        }
        self._pid: int | None = None
        self._primary_thread_id: int | None = None
        self._initial_image_ref: str | None = None
        self._qpc_start: int | None = None
        self._qpc_end: int | None = None
        self._subject_proof: str | None = None
        self._callbacks = 0
        self._payload_bytes = 0
        self._child_records = 0
        self._global_reasons: set[str] = set()
        self._cleanup_proved = False
        self._cleanup_uncertain = False
        self._probe_available = {
            "file_access": False,
            "dll_image_load": False,
            "registry_access": False,
            "code_integrity_policy": False,
        }
        self._schema_proved = {
            "file_access": False,
            "dll_image_load": False,
            "registry_access": False,
            "code_integrity_policy": False,
        }
        self._negative_window_closure = {
            "dll_image_load": False,
            "code_integrity_policy": False,
        }
        self._manifest_bindings: dict[str, str] = {}
        self._manifest_schema_uncertain: set[str] = set()
        self._kernel_schema_uncertain: set[str] = set()
        self._kernel_scope_uncertain: set[str] = set()
        self._kernel_session_snapshots: dict[str, KernelSessionSnapshot] = {}
        self._kernel_session_stage_order: list[str] = []
        self._scope_complete = {plane: True for plane in PLANE_ORDER}
        # Static inventory alone closes none of these planes.  File/Registry
        # may become complete only through `_kernel_negative_window_closed`'s
        # exact session/schema/TTID/generation/loss/cleanup conjunction.  DLL
        # route selection and CI descriptor semantics remain incomplete even
        # when individual observations are exact.
        self._scope_complete["file_access"] = False
        self._scope_complete["registry_access"] = False
        self._scope_complete["dll_image_load"] = False
        self._scope_complete["code_integrity_policy"] = False
        self._correlation_complete = {plane: True for plane in PLANE_ORDER}
        self._lossless = True
        self._overflowed = False
        self._lock = threading.RLock()

    def prebind_subject(self, *, pid: int, primary_thread_id: int) -> None:
        """Bind held process/thread identities before any session can emit."""

        with self._lock:
            if (
                self._pid is not None
                or type(pid) is not int
                or pid <= 0
                or type(primary_thread_id) is not int
                or primary_thread_id <= 0
            ):
                _fail("trace_binding_invalid")
            self._pid = pid
            self._primary_thread_id = primary_thread_id
            self._target_threads.add(primary_thread_id)
            self._thread_epochs[primary_thread_id] = _ThreadEpoch(
                pid, None, None, True
            )

    def bind(
        self,
        *,
        pid: int,
        qpc_start: int,
        initial_image_ref: str,
        primary_thread_id: int | None = None,
    ) -> None:
        with self._lock:
            if (
                self._qpc_start is not None
                or type(pid) is not int
                or pid <= 0
                or type(qpc_start) is not int
                or qpc_start <= 0
                or type(initial_image_ref) is not str
                or not self._inventory.contains_path(
                    "dll_image_load", initial_image_ref
                )
            ):
                _fail("trace_binding_invalid")
            if primary_thread_id is not None and (
                type(primary_thread_id) is not int or primary_thread_id <= 0
            ):
                _fail("trace_binding_invalid")
            if self._pid is not None and self._pid != pid:
                _fail("trace_binding_invalid")
            if (
                self._primary_thread_id is not None
                and primary_thread_id != self._primary_thread_id
            ):
                _fail("trace_binding_invalid")
            self._pid = pid
            self._initial_image_ref = initial_image_ref
            self._qpc_start = qpc_start
            self._primary_thread_id = primary_thread_id
            if primary_thread_id is not None:
                self._target_threads.add(primary_thread_id)
                existing_epoch = self._thread_epochs.get(primary_thread_id)
                if existing_epoch is None:
                    self._thread_epochs[primary_thread_id] = _ThreadEpoch(
                        pid, None, None, True
                    )
                elif existing_epoch.owner_pid != pid:
                    self._correlation_failure("file_access")
            # A successfully created suspended child proves the initial image was
            # mapped.  This is not an ETW rundown event and proves no CI absence.
            if self._record("dll_image_load"):
                self._success("dll_image_load", "image_map")

    def record_kernel_session_snapshot(
        self, *, stage: str, snapshot: KernelSessionSnapshot
    ) -> None:
        """Bind exact queried session state; caller-preseeded values never count."""

        with self._lock:
            if (
                stage not in {"start", "pre_stop", "stop"}
                or type(snapshot) is not KernelSessionSnapshot
                or stage in self._kernel_session_snapshots
                or stage
                != ("start", "pre_stop", "stop")[
                    len(self._kernel_session_stage_order)
                ]
            ):
                for plane in ("file_access", "registry_access"):
                    self._kernel_scope_uncertain.add(plane)
                    self._reason(plane, "plane_scope_unproved")
                return
            self._kernel_session_snapshots[stage] = snapshot
            self._kernel_session_stage_order.append(stage)
            if snapshot.loss_observed:
                self.mark_lost_events()
            if not snapshot.exact:
                for plane in ("file_access", "registry_access"):
                    self._kernel_scope_uncertain.add(plane)
                    self._reason(plane, "plane_scope_unproved")

    def thread_event(
        self, *, opcode: int, pid: int, thread_id: int, timestamp: int
    ) -> None:
        """Maintain bounded full TTID ownership epochs from Thread v3 payloads."""

        with self._lock:
            if (
                opcode not in {1, 2, 3, 4}
                or type(pid) is not int
                or type(thread_id) is not int
                or type(timestamp) is not int
                or not 0 <= pid <= 0xFFFFFFFF
                or not 0 <= thread_id <= 0xFFFFFFFF
            ):
                self.mark_schema_unknown("file_access")
                return
            if thread_id == 0:
                return
            if not self._kernel_setup_or_window(timestamp):
                return
            self._observe_kernel_timestamp(timestamp, "file_access")
            if (
                pid == self._pid
                and self._qpc_start is not None
                and (
                    timestamp == self._qpc_start
                    or (
                        self._qpc_end is not None
                        and timestamp == self._qpc_end
                    )
                )
            ):
                self._correlation_failure("file_access")
            existing = self._thread_epochs.get(thread_id)
            if opcode in {1, 3}:
                is_rundown = opcode == 3
                if existing is None:
                    if len(self._thread_epochs) >= MAX_TRACKED_THREADS:
                        self._reason("file_access", "observation_overflow")
                        return
                    epoch = _ThreadEpoch(
                        pid,
                        None if is_rundown else timestamp,
                    )
                    self._thread_epochs[thread_id] = epoch
                elif is_rundown and existing.end_timestamp is None:
                    if existing.owner_pid != pid:
                        self._correlation_failure("file_access")
                        self._thread_epochs[thread_id] = _ThreadEpoch(pid, None)
                    # DCStart is an inventory snapshot, not a start boundary.
                    epoch = self._thread_epochs[thread_id]
                else:
                    clean_reuse = bool(
                        existing.end_timestamp is not None
                        and timestamp > existing.end_timestamp
                    )
                    # A strictly ordered completed epoch may be reused.  Live,
                    # equal, or regressed reuse has no unique FileIo owner.
                    if not clean_reuse:
                        self._correlation_failure("file_access")
                    self._thread_epochs[thread_id] = _ThreadEpoch(
                        pid,
                        None if is_rundown else timestamp,
                    )
                    epoch = self._thread_epochs[thread_id]
                if epoch.owner_pid == self._pid and epoch.end_timestamp is None:
                    if (
                        thread_id not in self._target_threads
                        and len(self._target_threads) >= MAX_TARGET_THREADS
                    ):
                        self._reason("file_access", "observation_overflow")
                    else:
                        self._target_threads.add(thread_id)
                elif thread_id in self._target_threads:
                    self._target_threads.discard(thread_id)
                    self._correlation_failure("file_access")
                self._resolve_deferred_thread(thread_id)
                return
            if existing is None:
                if len(self._thread_epochs) >= MAX_TRACKED_THREADS:
                    self._reason("file_access", "observation_overflow")
                    return
                # An End still proves the owner immediately before the end;
                # the epoch started before the observed setup/window.
                self._thread_epochs[thread_id] = _ThreadEpoch(
                    pid, None, timestamp
                )
            else:
                invalid_end = bool(
                    existing.owner_pid != pid
                    or existing.end_timestamp is not None
                    or (
                        existing.start_timestamp is not None
                        and timestamp <= existing.start_timestamp
                    )
                    or (
                        existing.last_used_timestamp is not None
                        and timestamp <= existing.last_used_timestamp
                    )
                )
                if invalid_end:
                    self._correlation_failure("file_access")
                self._thread_epochs[thread_id] = _ThreadEpoch(
                    existing.owner_pid,
                    existing.start_timestamp,
                    timestamp,
                    existing.prebound,
                    existing.last_used_timestamp,
                )
            self._target_threads.discard(thread_id)
            self._resolve_deferred_thread(thread_id)

    def _thread_owner_for_file(
        self, thread_id: int, timestamp: int
    ) -> int | None:
        epoch = self._thread_epochs.get(thread_id)
        if epoch is None:
            return None
        if (
            epoch.start_timestamp is not None
            and timestamp <= epoch.start_timestamp
        ):
            # Equal-QPC delivery is explicitly unordered across processors;
            # an earlier timestamp belongs to an unmodeled older epoch.
            self._correlation_failure("file_access")
            return None
        if epoch.end_timestamp is not None and timestamp >= epoch.end_timestamp:
            self._correlation_failure("file_access")
            return None
        self._thread_epochs[thread_id] = _ThreadEpoch(
            epoch.owner_pid,
            epoch.start_timestamp,
            epoch.end_timestamp,
            epoch.prebound,
            max(timestamp, epoch.last_used_timestamp or timestamp),
        )
        return epoch.owner_pid

    def _resolve_deferred_thread(self, thread_id: int) -> None:
        retained: list[_DeferredFileBegin] = []
        for item in self._deferred_file_begins:
            if item.thread_id != thread_id:
                retained.append(item)
                continue
            owner = self._thread_owner_for_file(thread_id, item.timestamp)
            if owner == self._pid:
                self._accept_file_begin(item)
            elif owner is None:
                # The route is already sticky-inconclusive.  Do not retain a
                # record that can no longer be safely attributed.
                continue
            elif item.header_pid == self._pid:
                # Deferred and immediate ownership resolution apply the same
                # symmetric child-header/foreign-TTID contradiction rule.
                self._observe_foreign_file_lifetime(item)
                self._correlation_failure("file_access")
            else:
                self._observe_foreign_file_lifetime(item)
            # Otherwise an exact foreign owner closes this event as unrelated.
        self._deferred_file_begins = retained

    def _observe_foreign_file_lifetime(
        self, item: _DeferredFileBegin
    ) -> None:
        """Retire globally reused FileObject pointers without target attribution."""

        if item.lifetime_observed:
            self._observe_kernel_timestamp(item.timestamp, "file_access")
            return
        if item.operation not in {"file_create", "file_close"}:
            return
        if item.file_object <= 0:
            return
        self._observe_kernel_timestamp(item.timestamp, "file_access")
        self._transition_binding(
            self._file_objects,
            key=item.file_object,
            object_ref=None,
            timestamp=item.timestamp,
            plane="file_access",
            cap=MAX_FILE_OBJECTS,
            retain_unseen_tombstone=True,
            allow_equal_identical=False,
        )

    def _observe_kernel_timestamp(self, timestamp: int, plane: str) -> None:
        if (
            self._last_kernel_timestamp is not None
            and timestamp < self._last_kernel_timestamp
        ):
            self._correlation_failure(plane)
        self._last_kernel_timestamp = max(
            timestamp, self._last_kernel_timestamp or timestamp
        )

    def _mark_target_kernel_time(self, plane: str, timestamp: int) -> bool:
        if (
            self._qpc_start is None
            or timestamp <= self._qpc_start
            or (self._qpc_end is not None and timestamp >= self._qpc_end)
        ):
            self._correlation_failure(plane)
            return False
        latest = self._target_kernel_latest[plane]
        self._target_kernel_latest[plane] = max(timestamp, latest or timestamp)
        return True

    def mark_unknown_thread_event(self, *, timestamp: int) -> None:
        with self._lock:
            if self._kernel_setup_or_window(timestamp):
                self.mark_schema_unknown("file_access")

    def _kernel_setup_or_window(self, timestamp: int) -> bool:
        # Callback arrival order is not an event-time boundary.  Buffered
        # setup/window lifecycle records may arrive after bind or end_window;
        # their QPC timestamp remains authoritative through qpc_end.
        return bool(
            type(timestamp) is int
            and timestamp > 0
            and self._pid is not None
            and (self._qpc_end is None or timestamp <= self._qpc_end)
        )

    def end_window(self, qpc_end: int) -> None:
        with self._lock:
            if (
                self._qpc_start is None
                or self._qpc_end is not None
                or type(qpc_end) is not int
                or qpc_end <= self._qpc_start
            ):
                self._global_reasons.add("observation_ambiguous")
                return
            self._qpc_end = qpc_end
            for plane, latest in self._target_kernel_latest.items():
                if latest is not None and latest >= qpc_end:
                    self._correlation_failure(plane)

    def record_subject_proof(self, proof: str) -> None:
        with self._lock:
            if self._subject_proof is not None or proof != SUBJECT_ACCESS_DENIED:
                self._global_reasons.add("observation_ambiguous")
                return
            self._subject_proof = proof

    def begin_callback(self) -> bool:
        with self._lock:
            self._callbacks += 1
            if self._callbacks > MAX_CALLBACKS:
                self._overflowed = True
                self._global_reasons.add("observation_overflow")
                return False
            return True

    def inspect_payload(self, payload_bytes: int) -> bool:
        with self._lock:
            if type(payload_bytes) is not int or payload_bytes < 0:
                self._global_reasons.add("observation_ambiguous")
                return False
            if self._payload_bytes + payload_bytes > MAX_INSPECTED_PAYLOAD_BYTES:
                self._overflowed = True
                self._global_reasons.add("observation_overflow")
                return False
            self._payload_bytes += payload_bytes
            return True

    def _in_window(self, pid: int, timestamp: int) -> bool:
        return bool(
            self._pid is not None
            and pid == self._pid
            and self._qpc_start is not None
            and timestamp >= self._qpc_start
            and (self._qpc_end is None or timestamp <= self._qpc_end)
        )

    def _timestamp_in_window(self, timestamp: int) -> bool:
        return bool(
            type(timestamp) is int
            and self._qpc_start is not None
            and timestamp >= self._qpc_start
            and (self._qpc_end is None or timestamp <= self._qpc_end)
        )

    def _record(self, plane: str) -> bool:
        self._child_records += 1
        if self._child_records > MAX_CHILD_RECORDS:
            self._overflowed = True
            self._global_reasons.add("observation_overflow")
            return False
        return True

    def _reason(self, plane: str, reason: str) -> None:
        if reason not in _REASONS:
            reason = "observation_ambiguous"
        assert self._planes[plane].reasons is not None
        self._planes[plane].reasons.add(reason)
        if reason == "plane_scope_unproved":
            self._scope_complete[plane] = False
            if plane in {"file_access", "registry_access"}:
                self._kernel_scope_uncertain.add(plane)
        elif reason == "observation_overflow":
            self._overflowed = True
        elif reason == "observation_ambiguous":
            self._correlation_complete[plane] = False

    def _correlation_failure(self, plane: str) -> None:
        self._correlation_complete[plane] = False
        self._reason(plane, "observation_ambiguous")

    def _transition_binding(
        self,
        mapping: dict[int, _TimedBinding],
        *,
        key: int,
        object_ref: str | None,
        timestamp: int,
        plane: str,
        cap: int,
        retain_unseen_tombstone: bool = False,
        allow_equal_identical: bool = True,
    ) -> int | None:
        """Apply one bounded pointer generation without stale resurrection."""

        existing = mapping.get(key)
        if existing is None:
            if object_ref is None and not retain_unseen_tombstone:
                return None
            if len(mapping) >= cap:
                self._reason(plane, "observation_overflow")
                return None
            mapping[key] = _TimedBinding(object_ref, timestamp, 1)
            return 1
        if timestamp < existing.timestamp:
            self._correlation_failure(plane)
            mapping[key] = _TimedBinding(
                None,
                existing.timestamp,
                existing.generation + 1,
                existing.last_used_timestamp,
            )
            return None
        if (
            existing.last_used_timestamp is not None
            and timestamp <= existing.last_used_timestamp
        ):
            self._correlation_failure(plane)
            generation = existing.generation + 1
            mapping[key] = _TimedBinding(
                None,
                max(timestamp, existing.timestamp),
                generation,
                existing.last_used_timestamp,
            )
            return generation
        if timestamp == existing.timestamp:
            if allow_equal_identical and existing.object_ref == object_ref:
                return existing.generation
            self._correlation_failure(plane)
            generation = existing.generation + 1
            mapping[key] = _TimedBinding(None, timestamp, generation)
            return generation
        generation = existing.generation + 1
        if (
            existing.object_ref is not None
            and object_ref is not None
            and existing.object_ref != object_ref
        ):
            # A different identity needs an observed tombstone/lifetime edge.
            self._correlation_failure(plane)
            mapping[key] = _TimedBinding(None, timestamp, generation)
            return generation
        mapping[key] = _TimedBinding(object_ref, timestamp, generation)
        return generation

    def _mark_binding_used(
        self,
        mapping: dict[int, _TimedBinding],
        *,
        key: int,
        timestamp: int,
        plane: str,
        start_timestamp: int | None = None,
    ) -> _TimedBinding | None:
        existing = mapping.get(key)
        if existing is None:
            return None
        use_start = timestamp if start_timestamp is None else start_timestamp
        invalid = use_start <= existing.timestamp or timestamp < use_start
        if invalid:
            self._correlation_failure(plane)
            updated = _TimedBinding(
                None,
                max(existing.timestamp, timestamp),
                existing.generation + 1,
                max(timestamp, existing.last_used_timestamp or timestamp),
            )
        else:
            updated = _TimedBinding(
                existing.object_ref,
                existing.timestamp,
                existing.generation,
                max(timestamp, existing.last_used_timestamp or timestamp),
            )
        mapping[key] = updated
        return updated

    def _commit_binding(
        self,
        mapping: dict[int, _TimedBinding],
        *,
        key: int,
        object_ref: str,
        timestamp: int,
        expected_generation: int | None,
        plane: str,
    ) -> None:
        """Commit only the strictly-later completion of the current generation."""

        existing = mapping.get(key)
        if (
            expected_generation is None
            or existing is None
            or existing.generation != expected_generation
            or existing.object_ref is not None
            or timestamp <= existing.timestamp
        ):
            self._correlation_failure(plane)
            return
        mapping[key] = _TimedBinding(
            object_ref, timestamp, expected_generation
        )

    def _advance_binding_barrier(self, namespace: str, timestamp: int) -> None:
        current = self._binding_barriers[namespace]
        self._binding_barriers[namespace] = max(timestamp, current or timestamp)

    def _binding_barrier_allows(
        self, namespace: str, timestamp: int, plane: str
    ) -> bool:
        barrier = self._binding_barriers[namespace]
        if barrier is not None and timestamp <= barrier:
            self._correlation_failure(plane)
            return False
        return True

    def _success(self, plane: str, operation: str) -> None:
        if operation not in _OPERATIONS[plane]:
            self._reason(plane, "observation_ambiguous")
            return
        self._planes[plane].successes += 1
        assert self._planes[plane].operations is not None
        self._planes[plane].operations.add(operation)

    def _denial(
        self, plane: str, object_ref: str | None, operation: str
    ) -> None:
        if operation not in _OPERATIONS[plane]:
            self._reason(plane, "observation_ambiguous")
            return
        if object_ref is None or not self._inventory.contains(plane, object_ref):
            self._reason(plane, "plane_scope_unproved")
            return
        assert self._planes[plane].operations is not None
        assert self._planes[plane].denials is not None
        self._planes[plane].operations.add(operation)
        self._planes[plane].denials.add((object_ref, operation))

    def mark_lost_events(self) -> None:
        with self._lock:
            self._lossless = False
            self._global_reasons.add("observation_ambiguous")
            self._kernel_scope_uncertain.update(
                {"file_access", "registry_access"}
            )

    def mark_schema_unknown(self, plane: str) -> None:
        with self._lock:
            if plane in self._planes:
                self._schema_proved[plane] = False
                if plane in {"file_access", "registry_access"}:
                    self._kernel_schema_uncertain.add(plane)
                    self._kernel_scope_uncertain.add(plane)
                if plane in self._negative_window_closure:
                    self._negative_window_closure[plane] = False
                    self._manifest_schema_uncertain.add(plane)
                self._reason(plane, "observation_ambiguous")
            else:
                for item in PLANE_ORDER:
                    self._schema_proved[item] = False
                self._global_reasons.add("observation_ambiguous")

    def mark_unknown_kernel_event(
        self, *, plane: str, pid: int, timestamp: int
    ) -> None:
        """Downgrade only an exact target-child event in the bound QPC window."""

        with self._lock:
            if plane not in {"file_access", "registry_access"}:
                self._global_reasons.add("observation_ambiguous")
                return
            if type(pid) is not int or type(timestamp) is not int:
                self._global_reasons.add("observation_ambiguous")
                return
            if self._in_window(pid, timestamp):
                self.mark_schema_unknown(plane)

    def mark_unknown_file_completion_version(self, *, timestamp: int) -> None:
        """Downgrade a completion drift only while a target IRP is pending."""

        with self._lock:
            if type(timestamp) is not int:
                self._global_reasons.add("observation_ambiguous")
                return
            if self._pending and self._timestamp_in_window(timestamp):
                self.mark_schema_unknown("file_access")

    def mark_manifest_unavailable(self, plane: str) -> None:
        with self._lock:
            if plane in self._planes:
                self._probe_available[plane] = False
                self._schema_proved[plane] = False
                if plane in self._negative_window_closure:
                    self._negative_window_closure[plane] = False
                self._reason(plane, "probe_unavailable")

    def mark_probe_available(self, plane: str) -> None:
        with self._lock:
            if plane not in self._planes:
                self._global_reasons.add("observation_ambiguous")
                return
            self._probe_available[plane] = True
            if plane not in self._kernel_schema_uncertain:
                self._schema_proved[plane] = True

    def bind_manifest_identity(self, plane: str, binding: str) -> None:
        """Bind a verified full provider descriptor set without retaining it."""

        with self._lock:
            if (
                plane not in self._negative_window_closure
                or type(binding) is not str
                or _PROVIDER_BINDING.fullmatch(binding) is None
                or plane in self._manifest_bindings
            ):
                if plane in self._planes:
                    self._reason(plane, "observation_ambiguous")
                else:
                    self._global_reasons.add("observation_ambiguous")
                return
            self._manifest_bindings[plane] = binding

    def mark_manifest_available(self, plane: str) -> None:
        """Authorize silence only after exact full-host closure was bound."""

        with self._lock:
            if (
                plane not in self._negative_window_closure
                or plane not in self._manifest_bindings
                or plane in self._manifest_schema_uncertain
            ):
                self.mark_manifest_unavailable(plane)
                return
            self._probe_available[plane] = True
            self._schema_proved[plane] = True
            self._negative_window_closure[plane] = True

    def _mark_exact_manifest_event(self, plane: str) -> None:
        if plane not in self._negative_window_closure:
            self._global_reasons.add("observation_ambiguous")
            return
        self._probe_available[plane] = True
        if plane not in self._manifest_schema_uncertain:
            self._schema_proved[plane] = True

    def expects_file_completion(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def file_name(
        self,
        *,
        timestamp: int,
        file_object: int,
        raw_identity: str,
        remove: bool = False,
    ) -> None:
        """Reduce a FileIo name lifecycle event without retaining its path."""

        with self._lock:
            if not self._kernel_setup_or_window(timestamp):
                return
            if type(file_object) is not int or file_object <= 0:
                self._correlation_failure("file_access")
                return
            self._observe_kernel_timestamp(timestamp, "file_access")
            if remove:
                self._advance_binding_barrier("file_key", timestamp)
                if file_object in self._file_keys:
                    self._transition_binding(
                        self._file_keys,
                        key=file_object,
                        object_ref=None,
                        timestamp=timestamp,
                        plane="file_access",
                        cap=MAX_FILE_OBJECTS,
                    )
                return
            object_ref = self._inventory.resolve("file_access", raw_identity)
            if object_ref is None:
                self._advance_binding_barrier("file_key", timestamp)
                if file_object in self._file_keys:
                    self._transition_binding(
                        self._file_keys,
                        key=file_object,
                        object_ref=None,
                        timestamp=timestamp,
                        plane="file_access",
                        cap=MAX_FILE_OBJECTS,
                    )
                return
            if not self._binding_barrier_allows(
                "file_key", timestamp, "file_access"
            ):
                if file_object in self._file_keys:
                    self._transition_binding(
                        self._file_keys,
                        key=file_object,
                        object_ref=None,
                        timestamp=timestamp,
                        plane="file_access",
                        cap=MAX_FILE_OBJECTS,
                    )
                return
            self._transition_binding(
                self._file_keys,
                key=file_object,
                object_ref=object_ref,
                timestamp=timestamp,
                plane="file_access",
                cap=MAX_FILE_OBJECTS,
            )

    def mark_cleanup_uncertain(self) -> None:
        with self._lock:
            self._cleanup_uncertain = True
            self._cleanup_proved = False
            self._global_reasons.add("cleanup_unproved")

    def mark_cleanup_proved(self) -> None:
        with self._lock:
            if self._cleanup_uncertain:
                return
            self._cleanup_proved = True

    def mark_duration_overflow(self) -> None:
        with self._lock:
            self._overflowed = True
            self._global_reasons.add("observation_overflow")

    def file_begin(
        self,
        *,
        timestamp: int,
        irp: int,
        raw_identity: str | None,
        pid: int | None = None,
        thread_id: int | None = None,
        header_pid: int | None = None,
        file_object: int = 0,
        file_key: int = 0,
        operation: str = "file_create",
    ) -> None:
        with self._lock:
            # `pid` is retained only for old pure reducer vectors.  Native
            # translation always supplies the FileIo payload TTID because the
            # classic event header PID can be a sentinel.
            legacy_exact_pid = bool(
                thread_id is None
                and type(pid) is int
                and self._in_window(pid, timestamp)
            )
            if not self._timestamp_in_window(timestamp) and not legacy_exact_pid:
                return
            if (
                type(irp) is not int
                or irp <= 0
                or (thread_id is not None and (
                    type(thread_id) is not int
                    or not 0 <= thread_id <= 0xFFFFFFFF
                ))
                or type(file_object) is not int
                or file_object < 0
                or type(file_key) is not int
                or file_key < 0
                or type(operation) is not str
            ):
                self._correlation_failure("file_access")
                return
            direct_ref = (
                self._inventory.resolve("file_access", raw_identity)
                if raw_identity is not None
                else None
            )
            object_state = (
                self._file_objects.get(file_object) if file_object > 0 else None
            )
            key_state = self._file_keys.get(file_key) if file_key > 0 else None
            mapped_object_ref = (
                object_state.object_ref if object_state is not None else None
            )
            mapped_key_ref = key_state.object_ref if key_state is not None else None
            identity_ambiguous = bool(
                mapped_object_ref is not None
                and mapped_key_ref is not None
                and mapped_object_ref != mapped_key_ref
            )
            tombstoned = bool(
                operation != "file_create"
                and (
                    (object_state is not None and mapped_object_ref is None)
                    or (key_state is not None and mapped_key_ref is None)
                )
            )
            mapped_ref = (
                None if tombstoned else mapped_object_ref or mapped_key_ref
            )
            identity_ambiguous = identity_ambiguous or bool(
                direct_ref is not None
                and mapped_ref is not None
                and direct_ref != mapped_ref
            )
            # Create's OpenPath is sole identity authority.  A FileObject map
            # is only a conflict check, never a fallback across pointer reuse.
            object_ref = (
                direct_ref if operation == "file_create" else mapped_ref
            )
            lifetime_observed = bool(
                operation in {"file_create", "file_close"}
                and file_object > 0
            )
            object_generation: int | None = None
            if lifetime_observed:
                # FileObject is a global kernel pointer namespace.  Every
                # exact Create/Close is therefore a generation edge before
                # TTID ownership filtering.  Foreign identities are never
                # promoted; only a sanitized tombstone is retained.
                object_generation = self._transition_binding(
                    self._file_objects,
                    key=file_object,
                    object_ref=None,
                    timestamp=timestamp,
                    plane="file_access",
                    cap=MAX_FILE_OBJECTS,
                    retain_unseen_tombstone=True,
                    allow_equal_identical=False,
                )
            item = _DeferredFileBegin(
                timestamp,
                irp,
                object_ref,
                thread_id if type(thread_id) is int else 0,
                header_pid if type(header_pid) is int else 0,
                file_object,
                file_key,
                operation,
                identity_ambiguous,
                object_generation,
                lifetime_observed,
            )
            if thread_id == 0:
                if lifetime_observed:
                    self._observe_kernel_timestamp(
                        timestamp, "file_access"
                    )
                if header_pid == self._pid:
                    self._correlation_failure("file_access")
                return
            if legacy_exact_pid:
                self._accept_file_begin(item)
                return
            if thread_id is None:
                # The legacy pure seam has no TTID authority.  A foreign PID
                # is unrelated and must never fall into native deferral logic.
                if lifetime_observed:
                    self._observe_kernel_timestamp(
                        timestamp, "file_access"
                    )
                return
            assert type(thread_id) is int
            owner = self._thread_owner_for_file(thread_id, timestamp)
            if owner == self._pid:
                self._accept_file_begin(item)
                return
            if owner is not None:
                # Full Thread-v3 ownership proves this system FileIo event is
                # unrelated unless the classic header simultaneously names
                # the exact child.  That symmetric identity contradiction is
                # fail-closed even though payload TTID remains the authority.
                self._observe_foreign_file_lifetime(item)
                if header_pid == self._pid:
                    self._correlation_failure("file_access")
                return
            if len(self._deferred_file_begins) >= MAX_DEFERRED_FILE_BEGINS:
                self._reason("file_access", "observation_overflow")
                return
            self._deferred_file_begins.append(item)

    def _accept_file_begin(self, item: _DeferredFileBegin) -> None:
        self._observe_kernel_timestamp(item.timestamp, "file_access")
        if not self._mark_target_kernel_time("file_access", item.timestamp):
            return
        if item.identity_ambiguous:
            if (
                item.operation == "file_create"
                and item.file_object > 0
                and not item.lifetime_observed
            ):
                # Every Create is a FileObject generation boundary even when
                # OpenPath conflicts with the old pointer map.  Never leave
                # that stale map available to a later target operation.
                self._transition_binding(
                    self._file_objects,
                    key=item.file_object,
                    object_ref=None,
                    timestamp=item.timestamp,
                    plane="file_access",
                    cap=MAX_FILE_OBJECTS,
                    retain_unseen_tombstone=True,
                    allow_equal_identical=False,
                )
            self._correlation_failure("file_access")
            return
        if item.header_pid not in {0, 0xFFFFFFFF, self._pid}:
            self._correlation_failure("file_access")
            return
        if not self._record("file_access"):
            return
        if item.irp in self._pending or len(self._pending) >= MAX_PENDING_IRPS:
            if len(self._pending) >= MAX_PENDING_IRPS:
                self._reason("file_access", "observation_overflow")
            else:
                self._correlation_failure("file_access")
            return
        object_ref = item.object_ref
        file_object = item.file_object
        object_generation = item.object_generation
        if item.operation == "file_create" and file_object > 0:
            if not item.lifetime_observed:
                object_generation = self._transition_binding(
                    self._file_objects,
                    key=file_object,
                    object_ref=None,
                    timestamp=item.timestamp,
                    plane="file_access",
                    cap=MAX_FILE_OBJECTS,
                    retain_unseen_tombstone=True,
                    allow_equal_identical=False,
                )
        elif item.operation != "file_create":
            for mapping, key in (
                (self._file_objects, file_object),
                (self._file_keys, item.file_key),
            ):
                if key <= 0:
                    continue
                if (
                    item.operation == "file_close"
                    and item.lifetime_observed
                    and mapping is self._file_objects
                ):
                    continue
                used = self._mark_binding_used(
                    mapping,
                    key=key,
                    timestamp=item.timestamp,
                    plane="file_access",
                )
                if used is not None and (
                    used.object_ref is None
                    or (
                        object_ref is not None
                        and used.object_ref != object_ref
                    )
                ):
                    if used.object_ref is not None and object_ref is not None:
                        self._correlation_failure("file_access")
                    object_ref = None
        if object_ref is None:
            self._reason("file_access", "plane_scope_unproved")
        self._pending[item.irp] = _PendingFile(
            object_ref,
            item.operation,
            file_object,
            item.thread_id,
            item.timestamp,
            object_generation,
        )

    def file_complete(
        self,
        *,
        timestamp: int,
        irp: int,
        status: int,
        exact_pid_scope: bool,
    ) -> None:
        with self._lock:
            if (
                type(timestamp) is not int
                or type(irp) is not int
                or irp <= 0
                or type(status) is not int
                or not 0 <= status <= 0xFFFFFFFF
                or type(exact_pid_scope) is not bool
            ):
                self.mark_schema_unknown("file_access")
                return
            if (
                self._qpc_start is None
                or timestamp < self._qpc_start
                or (self._qpc_end is not None and timestamp > self._qpc_end)
            ):
                return
            pending = self._pending.pop(irp, None)
            if pending is None:
                if exact_pid_scope:
                    self._correlation_failure("file_access")
                return
            self._observe_kernel_timestamp(timestamp, "file_access")
            if not self._mark_target_kernel_time("file_access", timestamp):
                return
            if timestamp <= pending.begin_timestamp:
                # OpEnd has no TTID.  Only strict QPC causality with the
                # correlated begin can authorize its status classification.
                self._correlation_failure("file_access")
                return
            if status == _STATUS_ACCESS_DENIED:
                self._denial(
                    "file_access", pending.object_ref, pending.operation
                )
            elif pending.object_ref is None:
                self._reason("file_access", "plane_scope_unproved")
            else:
                # Every exactly decoded status other than ACCESS_DENIED is a
                # non-denial observation, including ordinary NOT_FOUND search.
                self._success("file_access", pending.operation)
                if (
                    pending.operation == "file_create"
                    and status == _STATUS_SUCCESS
                    and pending.file_object > 0
                ):
                    self._commit_binding(
                        self._file_objects,
                        key=pending.file_object,
                        object_ref=pending.object_ref,
                        timestamp=timestamp,
                        expected_generation=pending.object_generation,
                        plane="file_access",
                    )

    def image_load(
        self,
        *,
        pid: int,
        timestamp: int,
        raw_identity: str,
        is_rundown: bool = False,
    ) -> None:
        with self._lock:
            if is_rundown or not self._in_window(pid, timestamp):
                return
            if not self._record("dll_image_load"):
                return
            if self._inventory.resolve("dll_image_load", raw_identity) is None:
                self._reason("dll_image_load", "plane_scope_unproved")
            else:
                self._success("dll_image_load", "image_map")

    def registry_operation(
        self,
        *,
        pid: int,
        timestamp: int,
        raw_identity: str,
        status: int,
        operation: str,
        opcode: int | None = None,
        key_handle: int = 0,
        initial_time: int | None = None,
    ) -> None:
        with self._lock:
            if type(pid) is not int or type(timestamp) is not int:
                self._correlation_failure("registry_access")
                return
            if not self._in_window(pid, timestamp):
                return
            if (
                type(raw_identity) is not str
                or type(status) is not int
                or not 0 <= status <= 0xFFFFFFFF
                or type(operation) is not str
                or (
                    opcode is not None
                    and (
                        opcode not in _REGISTRY_OPERATION_BY_OPCODE
                        or opcode in _REGISTRY_LIFECYCLE_OPCODES
                        or operation != _REGISTRY_OPERATION_BY_OPCODE[opcode]
                    )
                )
            ):
                self.mark_schema_unknown("registry_access")
                return
            if opcode is not None and (
                type(initial_time) is not int
                or not -(1 << 63) <= initial_time < (1 << 63)
                or self._qpc_start is None
                or initial_time <= self._qpc_start
                or initial_time > timestamp
            ):
                self._correlation_failure("registry_access")
                return
            if not self._mark_target_kernel_time("registry_access", timestamp):
                return
            if (
                type(key_handle) is not int
                or key_handle < 0
                or not self._record("registry_access")
            ):
                self._correlation_failure("registry_access")
                return
            # Documented value-name opcodes require KCB/KeyHandle identity.
            # Direct-key opcodes use KeyName as sole authority; a KCB map may
            # corroborate it but may never replace it.
            direct_identity = (
                opcode in _REGISTRY_DIRECT_KEY_OPCODES or opcode is None
            )
            direct_ref = (
                self._inventory.resolve("registry_access", raw_identity)
                if direct_identity
                else None
            )
            mapped_state = self._registry_handles.get(key_handle)
            mapped_ref = (
                mapped_state.object_ref if mapped_state is not None else None
            )
            identity_conflict = bool(
                direct_ref is not None
                and mapped_ref is not None
                and direct_ref != mapped_ref
            )
            if identity_conflict and opcode not in {10, 11}:
                self._correlation_failure("registry_access")
                return
            if opcode in {10, 11} and key_handle > 0:
                # InitialTime begins an atomic handle generation.  Tombstone
                # the old KCB first, then commit only the strictly later exact
                # success record if no barrier/conflict crossed the interval.
                generation_start = (
                    initial_time if type(initial_time) is int else timestamp
                )
                barrier_allows = self._binding_barrier_allows(
                    "registry_handle", generation_start, "registry_access"
                )
                if (
                    mapped_state is not None
                    and mapped_state.timestamp >= generation_start
                ):
                    self._correlation_failure("registry_access")
                    barrier_allows = False
                if identity_conflict:
                    self._correlation_failure("registry_access")
                    barrier_allows = False
                generation = self._transition_binding(
                    self._registry_handles,
                    key=key_handle,
                    object_ref=None,
                    timestamp=generation_start,
                    plane="registry_access",
                    cap=MAX_REGISTRY_HANDLES,
                    retain_unseen_tombstone=True,
                    allow_equal_identical=False,
                )
                if (
                    barrier_allows
                    and status == _STATUS_SUCCESS
                    and direct_ref is not None
                ):
                    self._commit_binding(
                        self._registry_handles,
                        key=key_handle,
                        object_ref=direct_ref,
                        timestamp=timestamp,
                        expected_generation=generation,
                        plane="registry_access",
                    )
            # A direct KeyName is sole authority for create/open/delete.  The
            # old handle map is only a conflict check across pointer reuse.
            object_ref = direct_ref if direct_identity else mapped_ref
            self._observe_kernel_timestamp(timestamp, "registry_access")
            if not direct_identity and key_handle > 0:
                used = self._mark_binding_used(
                    self._registry_handles,
                    key=key_handle,
                    timestamp=timestamp,
                    start_timestamp=(
                        initial_time if type(initial_time) is int else timestamp
                    ),
                    plane="registry_access",
                )
                if used is not None and used.object_ref is None:
                    object_ref = None
            if status == _STATUS_ACCESS_DENIED:
                if operation in _OPERATIONS["registry_access"]:
                    self._denial("registry_access", object_ref, operation)
                else:
                    self._reason("registry_access", "plane_scope_unproved")
            elif object_ref is None:
                self._reason("registry_access", "plane_scope_unproved")
            else:
                if operation in _OPERATIONS["registry_access"]:
                    self._success("registry_access", operation)

    def registry_lifecycle(
        self,
        *,
        opcode: int,
        timestamp: int,
        key_handle: int,
        raw_identity: str | None,
        status: int,
        initial_time: int,
    ) -> None:
        """Maintain KCB bindings; lifecycle events never become denials."""

        with self._lock:
            if (
                opcode not in {22, 23, 24, 25, 27}
                or type(key_handle) is not int
                or key_handle < 0
                or type(status) is not int
                or not 0 <= status <= 0xFFFFFFFF
                or type(initial_time) is not int
                or not -(1 << 63) <= initial_time < (1 << 63)
            ):
                if self._kernel_setup_or_window(timestamp):
                    self._correlation_failure("registry_access")
                return
            if not self._kernel_setup_or_window(timestamp):
                return
            self._observe_kernel_timestamp(timestamp, "registry_access")
            invalid_initial_time = bool(
                initial_time != 0
                and (initial_time < 0 or initial_time > timestamp)
            )
            if (
                invalid_initial_time
                or status != _STATUS_SUCCESS
                or key_handle == 0
            ):
                if key_handle > 0:
                    self._advance_binding_barrier(
                        "registry_handle", timestamp
                    )
                    if key_handle in self._registry_handles:
                        self._transition_binding(
                            self._registry_handles,
                            key=key_handle,
                            object_ref=None,
                            timestamp=timestamp,
                            plane="registry_access",
                            cap=MAX_REGISTRY_HANDLES,
                        )
                self._correlation_failure("registry_access")
                return
            if opcode in {23, 27}:
                self._advance_binding_barrier("registry_handle", timestamp)
                if key_handle in self._registry_handles:
                    self._transition_binding(
                        self._registry_handles,
                        key=key_handle,
                        object_ref=None,
                        timestamp=timestamp,
                        plane="registry_access",
                        cap=MAX_REGISTRY_HANDLES,
                    )
                return
            object_ref = (
                self._inventory.resolve("registry_access", raw_identity)
                if raw_identity
                else None
            )
            if object_ref is None:
                self._advance_binding_barrier("registry_handle", timestamp)
                if key_handle in self._registry_handles:
                    self._transition_binding(
                        self._registry_handles,
                        key=key_handle,
                        object_ref=None,
                        timestamp=timestamp,
                        plane="registry_access",
                        cap=MAX_REGISTRY_HANDLES,
                    )
                return
            if not self._binding_barrier_allows(
                "registry_handle", timestamp, "registry_access"
            ):
                if key_handle in self._registry_handles:
                    self._transition_binding(
                        self._registry_handles,
                        key=key_handle,
                        object_ref=None,
                        timestamp=timestamp,
                        plane="registry_access",
                        cap=MAX_REGISTRY_HANDLES,
                    )
                return
            self._transition_binding(
                self._registry_handles,
                key=key_handle,
                object_ref=object_ref,
                timestamp=timestamp,
                plane="registry_access",
                cap=MAX_REGISTRY_HANDLES,
            )

    def user_loader_observation(
        self,
        *,
        pid: int,
        timestamp: int,
        semantic: str,
        raw_process_identity: str | None,
        raw_object_identity: str | None,
        failure_status: int | None = None,
    ) -> None:
        """Reduce one exact User-Loader template without retaining raw fields."""

        with self._lock:
            plane = "dll_image_load"
            if semantic not in {"status_denial", "fatal", "ancillary"}:
                self._reason(plane, "observation_ambiguous")
                return
            if not self._in_window(pid, timestamp):
                return
            if not self._record(plane):
                return
            self._mark_exact_manifest_event(plane)
            if raw_process_identity is not None:
                process_ref = self._inventory.resolve(
                    "dll_image_load", raw_process_identity
                )
                if process_ref != self._initial_image_ref:
                    self._reason(plane, "plane_scope_unproved")
                    return
            object_ref = None
            if raw_object_identity is not None:
                object_ref = (
                    self._inventory.resolve_loader_dependency(
                        raw_object_identity
                    )
                    if semantic == "status_denial"
                    else self._inventory.resolve(plane, raw_object_identity)
                )
            if semantic == "status_denial":
                if failure_status == _STATUS_ACCESS_DENIED:
                    self._denial(plane, object_ref, "image_map")
                else:
                    self._reason(plane, "observation_ambiguous")
            elif semantic == "fatal":
                self._reason(plane, "observation_ambiguous")
            elif raw_object_identity is not None and object_ref is None:
                self._reason(plane, "plane_scope_unproved")
            # Ancillary events never authorize an observed-no-denial result.
            # The successful initial image plus full provider/window closure
            # supplies that proof; this callback may only validate or downgrade.

    def code_integrity_observation(
        self,
        *,
        timestamp: int,
        semantic: str,
        raw_process_identity: str | None,
        raw_object_identity: str | None,
    ) -> None:
        """Bind CI by QPC and payload identities; header PID is not authority."""

        with self._lock:
            plane = "code_integrity_policy"
            if semantic not in {"denial", "fatal", "audit", "global_fatal"}:
                self._reason(plane, "observation_ambiguous")
                return
            if not self._timestamp_in_window(timestamp):
                return
            if semantic == "global_fatal":
                if self._record(plane):
                    self._mark_exact_manifest_event(plane)
                    self._reason(plane, "observation_ambiguous")
                return

            object_ref = (
                self._inventory.resolve(plane, raw_object_identity)
                if raw_object_identity is not None
                else None
            )
            if raw_process_identity is None:
                # CI header PID is not authority.  A provider event without an
                # exact payload process identity may be retained only as an
                # ambiguity and can never become a target-child denial.
                if self._record(plane):
                    self._mark_exact_manifest_event(plane)
                    self._correlation_failure(plane)
                return
            process_ref = self._inventory.resolve(
                "dll_image_load", raw_process_identity
            )
            if process_ref != self._initial_image_ref:
                if object_ref is not None:
                    self._reason(plane, "plane_scope_unproved")
                return
            if object_ref is None:
                self._reason(plane, "plane_scope_unproved")
                return
            if not self._record(plane):
                return
            self._mark_exact_manifest_event(plane)
            if semantic == "denial":
                self._denial(plane, object_ref, "image_policy_validate")
            elif semantic == "fatal":
                self._reason(plane, "observation_ambiguous")
            elif semantic == "audit":
                # A known allow/audit event is not an allow oracle.  Negative
                # evidence remains solely the verified full/lossless window.
                return

    def mark_unknown_manifest_event(
        self, *, plane: str, pid: int, timestamp: int
    ) -> None:
        with self._lock:
            in_window = (
                self._in_window(pid, timestamp)
                if plane == "dll_image_load"
                else self._timestamp_in_window(timestamp)
            )
            if in_window:
                self.mark_schema_unknown(plane)

    def _kernel_negative_window_closed(self, plane: str) -> bool:
        if plane not in {"file_access", "registry_access"}:
            return False
        return bool(
            set(self._kernel_session_snapshots) == {"start", "pre_stop", "stop"}
            and all(
                snapshot.exact
                for snapshot in self._kernel_session_snapshots.values()
            )
            and self._primary_thread_id is not None
            and self._pid is not None
            and self._qpc_start is not None
            and self._qpc_end is not None
            and self._qpc_end > self._qpc_start
            and self._probe_available[plane]
            and self._schema_proved[plane]
            and plane not in self._kernel_schema_uncertain
            and plane not in self._kernel_scope_uncertain
            and self._lossless
            and not self._overflowed
            and self._correlation_complete[plane]
            and self._cleanup_proved
            and (
                plane != "file_access"
                or (not self._pending and not self._deferred_file_begins)
            )
            and _kernel_partition_is_complete()
        )

    def finish(self) -> RuntimeTraceResult:
        with self._lock:
            if not self._cleanup_proved:
                self._global_reasons.add("cleanup_unproved")
            if self._qpc_end is None or self._pending:
                self._correlation_failure("file_access")
            if self._deferred_file_begins:
                self._correlation_failure("file_access")
            if self._subject_proof != SUBJECT_ACCESS_DENIED:
                self._global_reasons.add("observation_ambiguous")
            if "observation_ambiguous" in self._global_reasons:
                for plane in PLANE_ORDER:
                    self._correlation_complete[plane] = False
            planes: list[PlaneTraceResult] = []
            quality: list[PlaneTraceQuality] = []
            for plane in PLANE_ORDER:
                state = self._planes[plane]
                assert (
                    state.operations is not None
                    and state.denials is not None
                    and state.reasons is not None
                )
                operation = (
                    min(state.operations)
                    if state.operations
                    else _DEFAULT_OPERATION[plane]
                )
                negative_window_observed = bool(
                    (
                        plane in self._negative_window_closure
                        and self._negative_window_closure[plane]
                    )
                    or self._kernel_negative_window_closed(plane)
                )
                if (
                    not state.denials
                    and not state.successes
                    and not negative_window_observed
                ):
                    self._correlation_complete[plane] = False
                item_quality = PlaneTraceQuality(
                    plane,
                    COLLECTION_SCHEMA
                    if self._schema_proved[plane]
                    else UNKNOWN_COLLECTION_SCHEMA,
                    self._probe_available[plane],
                    self._lossless,
                    self._overflowed,
                    (
                        self._kernel_negative_window_closed(plane)
                        if plane in {"file_access", "registry_access"}
                        else self._scope_complete[plane]
                        and (
                            plane not in self._negative_window_closure
                            or bool(state.denials)
                            or self._negative_window_closure[plane]
                        )
                    ),
                    self._correlation_complete[plane],
                    self._cleanup_proved,
                )
                quality.append(item_quality)
                reason = _quality_failure_reason(item_quality)
                if reason is not None:
                    planes.append(
                        PlaneTraceResult(
                            plane,
                            "inconclusive",
                            None,
                            operation,
                            _POLICY[plane],
                            reason,
                        )
                    )
                elif state.denials:
                    object_ref, denial_operation = min(state.denials)
                    planes.append(
                        PlaneTraceResult(
                            plane,
                            "denial",
                            object_ref,
                            denial_operation,
                            _POLICY[plane],
                            None,
                        )
                    )
                elif state.successes or negative_window_observed:
                    planes.append(
                        PlaneTraceResult(
                            plane,
                            "observed_no_denial",
                            None,
                            operation,
                            _POLICY[plane],
                            None,
                        )
                    )
                else:
                    planes.append(
                        PlaneTraceResult(
                            plane,
                            "inconclusive",
                            None,
                            operation,
                            _POLICY[plane],
                            "observation_ambiguous",
                        )
                    )
            window_digest = hashlib.sha256()
            window_digest.update(b"taskgov-m241b-qpc-window-v1\0")
            for value in (
                self._inventory.runtime_digest,
                str(SESSION_GUID),
                str(self._pid or 0),
                str(self._primary_thread_id or 0),
                str(self._qpc_start or 0),
                str(self._qpc_end or 0),
                ",".join(sorted(self._kernel_session_snapshots)),
                self._manifest_bindings.get("dll_image_load", "manifest-absent"),
                self._manifest_bindings.get(
                    "code_integrity_policy", "manifest-absent"
                ),
            ):
                window_digest.update(value.encode("ascii"))
                window_digest.update(b"\0")
            return RuntimeTraceResult(
                CURRENT_RUNTIME_CANDIDATE,
                self._inventory.runtime_digest,
                self._subject_proof,
                min(self._callbacks, MAX_CALLBACKS),
                min(self._payload_bytes, MAX_INSPECTED_PAYLOAD_BYTES),
                min(self._child_records, MAX_CHILD_RECORDS),
                self._cleanup_proved,
                "window-sha256:" + window_digest.hexdigest(),
                tuple(quality),
                tuple(planes),
            )


class _TracePort(Protocol):
    def process_id(self, process_handle: int) -> int: ...
    def thread_binding(
        self, process_handle: int, thread_handle: int
    ) -> tuple[int, int]: ...
    def qpc(self) -> int: ...
    def session_present(self) -> bool: ...
    def start_session(self) -> int: ...
    def enable_session(self, owned_session_handle: int) -> frozenset[str]: ...
    def query_session(
        self, owned_session_handle: int
    ) -> KernelSessionSnapshot: ...
    def open_consumer(
        self,
        record_callback: Callable[[object], None],
        loss_callback: Callable[[], None],
    ) -> int: ...
    def process(self, trace_handle: int) -> int: ...
    def stop_session(self, owned_session_handle: int) -> StopTraceResult: ...
    def close_consumer(self, trace_handle: int) -> bool: ...


class RealtimeRuntimeCollector:
    """Own one exact ETW session; the caller retains the suspended child."""

    _READY_TIMEOUT_SECONDS = 2.0
    _JOIN_TIMEOUT_SECONDS = 2.0

    def __init__(
        self,
        inventory: InventoryBinding,
        *,
        port: _TracePort | None = None,
    ) -> None:
        self._reducer = _TraceReducer(inventory)
        self._port = _WindowsEtwPort(self._reducer) if port is None else port
        self._owned_session: int | None = None
        self._consumer: int | None = None
        self._process_handle: int | None = None
        self._pid: int | None = None
        self._primary_thread_id: int | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._timer: threading.Timer | None = None
        self._session_stopped = False
        self._consumer_closed = False
        self._terminal = False
        self._consumer_status: int | None = None
        self._stop_requested = False
        self._consumer_returned_early = False
        self._lock = threading.RLock()
        self._consumer_state_lock = threading.Lock()

    def start_for_suspended_child(
        self,
        *,
        process_id: int,
        process_handle: int,
        thread_handle: int,
        initial_image_object_ref: str,
    ) -> None:
        with self._lock:
            if (
                self._terminal
                or self._owned_session is not None
                or self._consumer is not None
            ):
                _fail("trace_invalid_state")
            if (
                type(process_id) is not int
                or process_id <= 0
                or type(process_handle) is not int
                or process_handle <= 0
                or type(thread_handle) is not int
                or thread_handle <= 0
            ):
                _fail("trace_binding_invalid")
            try:
                resolved_pid = self._port.process_id(process_handle)
                thread_pid, primary_thread_id = self._port.thread_binding(
                    process_handle, thread_handle
                )
                collision = self._port.session_present()
            except RuntimeTraceError:
                raise
            except BaseException:
                _fail("trace_unavailable")
            if (
                resolved_pid != process_id
                or thread_pid != process_id
                or type(primary_thread_id) is not int
                or primary_thread_id <= 0
            ):
                _fail("trace_binding_invalid")
            if collision:
                # Never adopt or stop a pre-existing exact-name session.
                _fail("trace_session_collision")
            self._reducer.prebind_subject(
                pid=process_id, primary_thread_id=primary_thread_id
            )
            self._process_handle = process_handle
            self._pid = process_id
            self._primary_thread_id = primary_thread_id
            try:
                owned = self._port.start_session()
                if type(owned) is not int or owned <= 0:
                    _fail("trace_unavailable")
                self._owned_session = owned
                capable_planes = self._port.enable_session(owned)
                if (
                    type(capable_planes) is not frozenset
                    or not capable_planes.issubset(set(PLANE_ORDER))
                ):
                    _fail("trace_unavailable")
                for plane in capable_planes:
                    if plane in {"dll_image_load", "code_integrity_policy"}:
                        self._reducer.mark_manifest_available(plane)
                    else:
                        self._reducer.mark_probe_available(plane)
                self._consumer = self._port.open_consumer(
                    self._record_callback, self._reducer.mark_lost_events
                )
                if type(self._consumer) is not int or self._consumer <= 0:
                    _fail("trace_unavailable")
                self._consumer_closed = False
                self._thread = threading.Thread(
                    target=self._consume,
                    name="taskgov-m241b-etw-consumer",
                    daemon=True,
                )
                self._thread.start()
                if not self._ready.wait(self._READY_TIMEOUT_SECONDS):
                    _fail("trace_unavailable")
                start_snapshot = self._port.query_session(owned)
                self._reducer.record_kernel_session_snapshot(
                    stage="start", snapshot=start_snapshot
                )
                with self._consumer_state_lock:
                    consumer_started_exactly = bool(
                        self._consumer_status is None
                        and not self._consumer_returned_early
                        and not self._stop_requested
                    )
                if (
                    not start_snapshot.exact
                    or self._thread is None
                    or not self._thread.is_alive()
                    or not consumer_started_exactly
                ):
                    _fail("trace_unavailable")
                qpc_start = self._port.qpc()
                self._reducer.bind(
                    pid=process_id,
                    qpc_start=qpc_start,
                    initial_image_ref=initial_image_object_ref,
                    primary_thread_id=primary_thread_id,
                )
                self._timer = threading.Timer(
                    MAX_DURATION_SECONDS, self._expire_session
                )
                self._timer.daemon = True
                self._timer.start()
            except BaseException as error:
                # A StartTrace collision is not our session.  Never query it as
                # cleanup state, adopt it, or issue a stop against it.
                if self._owned_session is None:
                    if isinstance(error, RuntimeTraceError):
                        raise
                    _fail("trace_unavailable")
                if not self._cleanup_after_start_failure():
                    _fail("trace_cleanup_unproved")
                if isinstance(error, RuntimeTraceError):
                    raise
                _fail("trace_unavailable")

    def _record_callback(self, record: object) -> None:
        # The native port supplies its already-bounded EVENT_RECORD pointer.  A
        # fake port may invoke reducer methods directly and need not use this.
        if isinstance(self._port, _WindowsEtwPort):
            self._port.translate(record)

    def _consume(self) -> None:
        self._ready.set()
        try:
            assert self._consumer is not None
            status = self._port.process(self._consumer)
        except BaseException:
            status = -1
        with self._consumer_state_lock:
            self._consumer_status = status
            if not self._stop_requested:
                self._consumer_returned_early = True
            returned_early = self._consumer_returned_early
        if returned_early:
            self._reducer.mark_cleanup_uncertain()

    def _stop_owned_once(self) -> bool:
        with self._lock:
            if self._owned_session is None:
                return True
            if self._session_stopped:
                return True
            try:
                # This lock makes ProcessTrace return publication and the
                # owned STOP request one ordered handshake.  A return that is
                # published first is sticky-early even if STOP races next.
                with self._consumer_state_lock:
                    self._stop_requested = True
                result = self._port.stop_session(self._owned_session)
            except BaseException:
                result = StopTraceResult(False, 0, 0, 0)
            if result.loss_observed:
                self._reducer.mark_lost_events()
            if result.snapshot is not None:
                self._reducer.record_kernel_session_snapshot(
                    stage="stop", snapshot=result.snapshot
                )
            else:
                self._reducer.record_kernel_session_snapshot(
                    stage="stop", snapshot=KernelSessionSnapshot(0, 0, 0, -1, -1, -1)
                )
            self._session_stopped = result.stopped
            return result.stopped

    def _close_consumer_once(self) -> bool:
        if self._consumer is None or self._consumer_closed:
            return True
        try:
            closed = self._port.close_consumer(self._consumer)
        except BaseException:
            closed = False
        if closed:
            self._consumer_closed = True
        return closed

    def _expire_session(self) -> None:
        with self._lock:
            if self._terminal:
                return
            self._reducer.mark_duration_overflow()
            if not self._stop_owned_once():
                self._reducer.mark_cleanup_uncertain()

    def record_subject_proof(self, proof: str) -> None:
        self._reducer.record_subject_proof(proof)

    def abort(self) -> None:
        """Best-effort closed cleanup for a still-suspended or failed probe."""

        with self._lock:
            self._terminal = True
            if self._timer is not None:
                self._timer.cancel()
            if self._owned_session is None and self._consumer is None:
                return
            okay = self._stop_owned_once()
            if self._thread is not None:
                self._thread.join(self._JOIN_TIMEOUT_SECONDS)
                okay = okay and not self._thread.is_alive()
            okay = self._close_consumer_once() and okay
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(self._JOIN_TIMEOUT_SECONDS)
                okay = okay and not self._thread.is_alive()
            self._reducer.mark_cleanup_uncertain()
            try:
                okay = (not self._port.session_present()) and okay
            except BaseException:
                okay = False
            if okay:
                self._owned_session = None
                self._consumer = None
            else:
                # Retain only handles returned by our own StartTrace/OpenTrace
                # so one later bounded abort can retry physical cleanup.
                _fail("trace_cleanup_unproved")

    def stop(self) -> RuntimeTraceResult:
        with self._lock:
            if (
                self._owned_session is None
                or self._consumer is None
                or self._thread is None
                or self._pid is None
                or self._process_handle is None
                or self._primary_thread_id is None
            ):
                _fail("trace_invalid_state")
            self._terminal = True
            if self._timer is not None:
                self._timer.cancel()
            with self._consumer_state_lock:
                consumer_active_at_pre_stop = bool(
                    self._consumer_status is None
                    and not self._consumer_returned_early
                    and not self._stop_requested
                )
            if not self._thread.is_alive() or not consumer_active_at_pre_stop:
                self._reducer.mark_cleanup_uncertain()
            try:
                pre_stop_snapshot = self._port.query_session(self._owned_session)
            except BaseException:
                pre_stop_snapshot = KernelSessionSnapshot(0, 0, 0, -1, -1, -1)
            self._reducer.record_kernel_session_snapshot(
                stage="pre_stop", snapshot=pre_stop_snapshot
            )
            try:
                qpc_end = self._port.qpc()
            except BaseException:
                qpc_end = -1
            self._reducer.end_window(qpc_end)
            try:
                exact_handle = self._port.process_id(self._process_handle) == self._pid
            except BaseException:
                exact_handle = False
            cleanup_ok = exact_handle and consumer_active_at_pre_stop
            if not exact_handle:
                self._reducer.mark_cleanup_uncertain()
            if not self._stop_owned_once():
                cleanup_ok = False
                self._reducer.mark_cleanup_uncertain()
            self._thread.join(self._JOIN_TIMEOUT_SECONDS)
            if self._thread.is_alive():
                cleanup_ok = False
                self._reducer.mark_cleanup_uncertain()
            # Required success order: Stop -> ProcessTrace join -> CloseTrace.
            if not self._close_consumer_once():
                cleanup_ok = False
                self._reducer.mark_cleanup_uncertain()
            if self._thread.is_alive():
                self._thread.join(self._JOIN_TIMEOUT_SECONDS)
            with self._consumer_state_lock:
                consumer_status = self._consumer_status
                consumer_returned_early = self._consumer_returned_early
            if self._thread.is_alive() or consumer_status != _ERROR_SUCCESS:
                cleanup_ok = False
                self._reducer.mark_cleanup_uncertain()
            if consumer_returned_early:
                cleanup_ok = False
                self._reducer.mark_cleanup_uncertain()
            try:
                absent = not self._port.session_present()
            except BaseException:
                absent = False
            if not absent:
                cleanup_ok = False
                self._reducer.mark_cleanup_uncertain()
            if cleanup_ok:
                self._reducer.mark_cleanup_proved()
            result = self._reducer.finish()
            if cleanup_ok:
                self._owned_session = None
                self._consumer = None
            return result

    def _cleanup_after_start_failure(self) -> bool:
        self._terminal = True
        if self._timer is not None:
            self._timer.cancel()
        okay = self._stop_owned_once()
        if self._thread is not None:
            self._thread.join(self._JOIN_TIMEOUT_SECONDS)
            okay = okay and not self._thread.is_alive()
        okay = self._close_consumer_once() and okay
        try:
            okay = (not self._port.session_present()) and okay
        except BaseException:
            okay = False
        if okay:
            self._owned_session = None
            self._consumer = None
            self._thread = None
        return okay


# ---- Direct Windows ETW port -------------------------------------------------

ULONG = ctypes.c_uint32
USHORT = ctypes.c_uint16
UCHAR = ctypes.c_ubyte
ULONGLONG = ctypes.c_uint64
LONGLONG = ctypes.c_int64
TRACEHANDLE = ctypes.c_uint64


class _GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", ULONG),
        ("Data2", USHORT),
        ("Data3", USHORT),
        ("Data4", UCHAR * 8),
    )

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> "_GUID":
        raw = value.bytes_le
        return cls(
            int.from_bytes(raw[0:4], "little"),
            int.from_bytes(raw[4:6], "little"),
            int.from_bytes(raw[6:8], "little"),
            (UCHAR * 8).from_buffer_copy(raw[8:]),
        )

    def key(self) -> bytes:
        return bytes(memoryview(self))


class _WNODE_HEADER(ctypes.Structure):
    _fields_ = (
        ("BufferSize", ULONG),
        ("ProviderId", ULONG),
        ("HistoricalContext", ULONGLONG),
        ("TimeStamp", LONGLONG),
        ("Guid", _GUID),
        ("ClientContext", ULONG),
        ("Flags", ULONG),
    )


class _EVENT_TRACE_PROPERTIES(ctypes.Structure):
    _fields_ = (
        ("Wnode", _WNODE_HEADER),
        ("BufferSize", ULONG),
        ("MinimumBuffers", ULONG),
        ("MaximumBuffers", ULONG),
        ("MaximumFileSize", ULONG),
        ("LogFileMode", ULONG),
        ("FlushTimer", ULONG),
        ("EnableFlags", ULONG),
        ("AgeLimit", ctypes.c_int32),
        ("NumberOfBuffers", ULONG),
        ("FreeBuffers", ULONG),
        ("EventsLost", ULONG),
        ("BuffersWritten", ULONG),
        ("LogBuffersLost", ULONG),
        ("RealTimeBuffersLost", ULONG),
        ("LoggerThreadId", ctypes.c_void_p),
        ("LogFileNameOffset", ULONG),
        ("LoggerNameOffset", ULONG),
    )


class _EVENT_DESCRIPTOR(ctypes.Structure):
    _fields_ = (
        ("Id", USHORT),
        ("Version", UCHAR),
        ("Channel", UCHAR),
        ("Level", UCHAR),
        ("Opcode", UCHAR),
        ("Task", USHORT),
        ("Keyword", ULONGLONG),
    )


class _PROVIDER_EVENT_INFO_HEADER(ctypes.Structure):
    _fields_ = (("NumberOfEvents", ULONG), ("Reserved", ULONG))


class _EVENT_HEADER(ctypes.Structure):
    _fields_ = (
        ("Size", USHORT),
        ("HeaderType", USHORT),
        ("Flags", USHORT),
        ("EventProperty", USHORT),
        ("ThreadId", ULONG),
        ("ProcessId", ULONG),
        ("TimeStamp", LONGLONG),
        ("ProviderId", _GUID),
        ("EventDescriptor", _EVENT_DESCRIPTOR),
        ("ProcessorTime", ULONGLONG),
        ("ActivityId", _GUID),
    )


class _ETW_BUFFER_CONTEXT(ctypes.Structure):
    _fields_ = (("ProcessorIndex", USHORT), ("LoggerId", USHORT))


class _EVENT_RECORD(ctypes.Structure):
    _fields_ = (
        ("EventHeader", _EVENT_HEADER),
        ("BufferContext", _ETW_BUFFER_CONTEXT),
        ("ExtendedDataCount", USHORT),
        ("UserDataLength", USHORT),
        ("ExtendedData", ctypes.c_void_p),
        ("UserData", ctypes.c_void_p),
        ("UserContext", ctypes.c_void_p),
    )


class _EVENT_TRACE_HEADER(ctypes.Structure):
    _fields_ = (
        ("Size", USHORT),
        ("FieldTypeFlags", USHORT),
        ("Version", ULONG),
        ("ThreadId", ULONG),
        ("ProcessId", ULONG),
        ("TimeStamp", LONGLONG),
        ("Guid", _GUID),
        ("ProcessorTime", ULONGLONG),
    )


class _EVENT_TRACE(ctypes.Structure):
    _fields_ = (
        ("Header", _EVENT_TRACE_HEADER),
        ("InstanceId", ULONG),
        ("ParentInstanceId", ULONG),
        ("ParentGuid", _GUID),
        ("MofData", ctypes.c_void_p),
        ("MofLength", ULONG),
        ("ClientContext", ULONG),
    )


class _SYSTEMTIME(ctypes.Structure):
    _fields_ = tuple((name, USHORT) for name in (
        "Year", "Month", "DayOfWeek", "Day", "Hour", "Minute", "Second", "Milliseconds"
    ))


class _TIME_ZONE_INFORMATION(ctypes.Structure):
    _fields_ = (
        ("Bias", ctypes.c_int32),
        ("StandardName", wintypes.WCHAR * 32),
        ("StandardDate", _SYSTEMTIME),
        ("StandardBias", ctypes.c_int32),
        ("DaylightName", wintypes.WCHAR * 32),
        ("DaylightDate", _SYSTEMTIME),
        ("DaylightBias", ctypes.c_int32),
    )


class _TRACE_LOGFILE_HEADER(ctypes.Structure):
    _fields_ = (
        ("BufferSize", ULONG),
        ("Version", ULONG),
        ("ProviderVersion", ULONG),
        ("NumberOfProcessors", ULONG),
        ("EndTime", LONGLONG),
        ("TimerResolution", ULONG),
        ("MaximumFileSize", ULONG),
        ("LogFileMode", ULONG),
        ("BuffersWritten", ULONG),
        ("LogInstanceGuid", _GUID),
        ("LoggerName", wintypes.LPWSTR),
        ("LogFileName", wintypes.LPWSTR),
        ("TimeZone", _TIME_ZONE_INFORMATION),
        ("BootTime", LONGLONG),
        ("PerfFreq", LONGLONG),
        ("StartTime", LONGLONG),
        ("ReservedFlags", ULONG),
        ("BuffersLost", ULONG),
    )


class _EVENT_TRACE_LOGFILEW(ctypes.Structure):
    pass


_CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
_EVENT_RECORD_CALLBACK = _CALLBACK(None, ctypes.POINTER(_EVENT_RECORD))
_BUFFER_CALLBACK = _CALLBACK(ULONG, ctypes.POINTER(_EVENT_TRACE_LOGFILEW))

_EVENT_TRACE_LOGFILEW._fields_ = (
    ("LogFileName", wintypes.LPWSTR),
    ("LoggerName", wintypes.LPWSTR),
    ("CurrentTime", LONGLONG),
    ("BuffersRead", ULONG),
    ("ProcessTraceMode", ULONG),
    ("CurrentEvent", _EVENT_TRACE),
    ("LogfileHeader", _TRACE_LOGFILE_HEADER),
    ("BufferCallback", _BUFFER_CALLBACK),
    ("BufferSize", ULONG),
    ("Filled", ULONG),
    ("EventsLost", ULONG),
    ("EventRecordCallback", _EVENT_RECORD_CALLBACK),
    ("IsKernelTrace", ULONG),
    ("Context", ctypes.c_void_p),
)


class _PROPERTY_DATA_DESCRIPTOR(ctypes.Structure):
    _fields_ = (
        ("PropertyName", ULONGLONG),
        ("ArrayIndex", ULONG),
        ("Reserved", ULONG),
    )


_ERROR_SUCCESS = 0
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_NOT_FOUND = 1168
_ERROR_WMI_INSTANCE_NOT_FOUND = 4201
_ERROR_ALREADY_EXISTS = 183
_INVALID_PROCESSTRACE_HANDLE = 0xFFFFFFFFFFFFFFFF
_WNODE_FLAG_TRACED_GUID = 0x00020000
_EVENT_TRACE_CONTROL_QUERY = 0
_EVENT_TRACE_CONTROL_STOP = 1
_EVENT_HEADER_FLAG_32_BIT_HEADER = 0x0020
_EVENT_HEADER_FLAG_64_BIT_HEADER = 0x0040
_EVENT_HEADER_FLAG_CLASSIC_HEADER = 0x0100
_CLASSIC_EVENT_ID = 0xFFFF
_PROCESS_TRACE_MODE_REAL_TIME = 0x00000100
_PROCESS_TRACE_MODE_RAW_TIMESTAMP = 0x00001000
_PROCESS_TRACE_MODE_EVENT_RECORD = 0x10000000
EXACT_PROCESS_TRACE_MODE = (
    _PROCESS_TRACE_MODE_REAL_TIME
    | _PROCESS_TRACE_MODE_RAW_TIMESTAMP
    | _PROCESS_TRACE_MODE_EVENT_RECORD
)
_EVENT_CONTROL_CODE_ENABLE_PROVIDER = 1
_TRACE_LEVEL_VERBOSE = 5

_FILE_PROVIDER_UUID = uuid.UUID("90cbdc39-4a3e-11d1-84f4-0000f80464e3")
_THREAD_PROVIDER_UUID = uuid.UUID("3d6fa8d1-fe05-11d0-9dda-00c04fd7ba7c")
_IMAGE_PROVIDER_UUID = uuid.UUID("2cb15d1d-5fc1-11d2-abe1-00a0c911f518")
_REGISTRY_PROVIDER_UUID = uuid.UUID("ae53722e-c863-11d2-8659-00c04fa321a1")
_FILE_PROVIDER = _GUID.from_uuid(_FILE_PROVIDER_UUID).key()
_THREAD_PROVIDER = _GUID.from_uuid(_THREAD_PROVIDER_UUID).key()
_IMAGE_PROVIDER = _GUID.from_uuid(_IMAGE_PROVIDER_UUID).key()
_REGISTRY_PROVIDER = _GUID.from_uuid(_REGISTRY_PROVIDER_UUID).key()
_USER_LOADER_PROVIDER_UUID = uuid.UUID("b059b83f-d946-4b13-87ca-4292839dc2f2")
_CI_PROVIDER_UUID = uuid.UUID("4ee76bd8-3cf4-44a0-a0ac-3937643e37a3")
_USER_LOADER_PROVIDER = _GUID.from_uuid(_USER_LOADER_PROVIDER_UUID).key()
_CI_PROVIDER = _GUID.from_uuid(_CI_PROVIDER_UUID).key()
_REGISTRY_OPERATION_BY_OPCODE = {
    10: "registry_create",
    11: "registry_open",
    12: "registry_delete",
    13: "registry_query",
    14: "registry_set_value",
    15: "registry_delete_value",
    16: "registry_query_value",
    17: "registry_enumerate_key",
    18: "registry_enumerate_value",
    19: "registry_query_multiple_values",
    20: "registry_set_information",
    21: "registry_flush",
    22: "registry_kcb_create",
    23: "registry_kcb_delete",
    24: "registry_kcb_rundown_begin",
    25: "registry_kcb_rundown_end",
    26: "registry_virtualize",
    27: "registry_close",
}

_FILE_OPERATION_BY_OPCODE = {
    64: "file_create",
    65: "file_cleanup",
    66: "file_close",
    67: "file_read",
    68: "file_write",
    69: "file_set_information",
    70: "file_delete",
    71: "file_rename",
    72: "file_directory_enumeration",
    73: "file_flush",
    74: "file_query_information",
    75: "file_system_control",
    76: "file_operation_end",
    77: "file_directory_notification",
}

_FILE_NAME_OPCODES = frozenset({0, 32, 35, 36})
_THREAD_OPCODES = frozenset({1, 2, 3, 4})
_REGISTRY_LIFECYCLE_OPCODES = frozenset({22, 23, 24, 25, 27})
_REGISTRY_DIRECT_KEY_OPCODES = frozenset({10, 11, 12, 17, 20, 21, 26})
_THREAD_V3_FIELDS = (
    ("ProcessId", "uint", 4),
    ("TThreadId", "uint", 4),
    ("StackBase", "pointer", 8),
    ("StackLimit", "pointer", 8),
    ("UserStackBase", "pointer", 8),
    ("UserStackLimit", "pointer", 8),
    ("Affinity", "pointer", 8),
    ("Win32StartAddr", "pointer", 8),
    ("TebBase", "pointer", 8),
    ("SubProcessTag", "uint", 4),
    ("BasePriority", "uint", 1),
    ("PagePriority", "uint", 1),
    ("IoPriority", "uint", 1),
    ("ThreadFlags", "uint", 1),
)


@dataclass(frozen=True, slots=True)
class _KernelEventTemplate:
    provider: uuid.UUID
    opcode: int
    version: int
    plane: str
    operation: str
    fields: tuple[tuple[str, str, int | None], ...]
    field_contract: str = "full_payload"


_KERNEL_EVENT_TEMPLATES = (
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            2,
            "file_access",
            "file_name",
            (("FileObject", "uint", 8), ("FileName", "utf16", None)),
        )
        for opcode in sorted(_FILE_NAME_OPCODES)
    ),
    _KernelEventTemplate(
        _FILE_PROVIDER_UUID,
        64,
        2,
        "file_access",
        "file_create",
        (
            ("IrpPtr", "uint", 8),
            ("TTID", "pointer", 8),
            ("FileObject", "uint", 8),
            ("CreateOptions", "uint", 4),
            ("FileAttributes", "uint", 4),
            ("ShareAccess", "uint", 4),
            ("OpenPath", "utf16", None),
        ),
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            2,
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("IrpPtr", "uint", 8),
                ("TTID", "pointer", 8),
                ("FileObject", "uint", 8),
                ("FileKey", "uint", 8),
            ),
        )
        for opcode in (65, 66, 73)
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            2,
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("Offset", "uint", 8),
                ("IrpPtr", "uint", 8),
                ("TTID", "pointer", 8),
                ("FileObject", "uint", 8),
                ("FileKey", "uint", 8),
                ("IoSize", "uint", 4),
                ("IoFlags", "uint", 4),
            ),
        )
        for opcode in (67, 68)
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            2,
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("IrpPtr", "uint", 8),
                ("TTID", "pointer", 8),
                ("FileObject", "uint", 8),
                ("FileKey", "uint", 8),
                ("ExtraInfo", "uint", 8),
                ("InfoClass", "uint", 4),
            ),
        )
        for opcode in (69, 70, 71, 74, 75)
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            2,
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("IrpPtr", "uint", 8),
                ("TTID", "pointer", 8),
                ("FileObject", "uint", 8),
                ("FileKey", "uint", 8),
                ("Length", "uint", 4),
                ("InfoClass", "uint", 4),
                ("FileIndex", "uint", 4),
                ("FileName", "utf16", None),
            ),
        )
        for opcode in (72, 77)
    ),
    _KernelEventTemplate(
        _FILE_PROVIDER_UUID,
        76,
        2,
        "file_access",
        "file_operation_end",
        (
            ("IrpPtr", "uint", 8),
            ("ExtraInfo", "uint", 8),
            ("NtStatus", "uint", 4),
        ),
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            3,
            "file_access",
            "file_name",
            (("FileObject", "pointer", 8), ("FileName", "utf16", None)),
        )
        for opcode in sorted(_FILE_NAME_OPCODES)
    ),
    _KernelEventTemplate(
        _FILE_PROVIDER_UUID,
        64,
        3,
        "file_access",
        "file_create",
        (
            ("IrpPtr", "pointer", 8),
            ("FileObject", "pointer", 8),
            ("TTID", "uint", 4),
            ("CreateOptions", "uint", 4),
            ("FileAttributes", "uint", 4),
            ("ShareAccess", "uint", 4),
            ("OpenPath", "utf16", None),
        ),
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            3,
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("IrpPtr", "pointer", 8),
                ("FileObject", "pointer", 8),
                ("FileKey", "pointer", 8),
                ("TTID", "uint", 4),
            ),
        )
        for opcode in (65, 66, 73)
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            3,
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("Offset", "uint", 8),
                ("IrpPtr", "pointer", 8),
                ("FileObject", "pointer", 8),
                ("FileKey", "pointer", 8),
                ("TTID", "uint", 4),
                ("IoSize", "uint", 4),
                ("IoFlags", "uint", 4),
            ),
        )
        for opcode in (67, 68)
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            3,
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("IrpPtr", "pointer", 8),
                ("FileObject", "pointer", 8),
                ("FileKey", "pointer", 8),
                ("ExtraInfo", "pointer", 8),
                ("TTID", "uint", 4),
                ("InfoClass", "uint", 4),
            ),
        )
        for opcode in (69, 70, 71, 74, 75)
    ),
    *(
        _KernelEventTemplate(
            _FILE_PROVIDER_UUID,
            opcode,
            3,
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("IrpPtr", "pointer", 8),
                ("FileObject", "pointer", 8),
                ("FileKey", "pointer", 8),
                ("TTID", "uint", 4),
                ("Length", "uint", 4),
                ("InfoClass", "uint", 4),
                ("FileIndex", "uint", 4),
                ("FileName", "utf16", None),
            ),
        )
        for opcode in (72, 77)
    ),
    _KernelEventTemplate(
        _FILE_PROVIDER_UUID,
        76,
        3,
        "file_access",
        "file_operation_end",
        (
            ("IrpPtr", "pointer", 8),
            ("ExtraInfo", "pointer", 8),
            ("NtStatus", "uint", 4),
        ),
    ),
    *(
        _KernelEventTemplate(
            _THREAD_PROVIDER_UUID,
            opcode,
            3,
            "file_access",
            "thread_lifecycle",
            _THREAD_V3_FIELDS,
            "consumed_attribution_subset",
        )
        for opcode in sorted(_THREAD_OPCODES)
    ),
    _KernelEventTemplate(
        _IMAGE_PROVIDER_UUID,
        10,
        2,
        "dll_image_load",
        "image_map",
        (("ProcessId", "uint", 4), ("FileName", "utf16", None)),
        "consumed_attribution_subset",
    ),
    *(
        _KernelEventTemplate(
            _REGISTRY_PROVIDER_UUID,
            opcode,
            2,
            "registry_access",
            operation,
            (
                ("InitialTime", "sint", 8),
                ("Status", "uint", 4),
                ("Index", "uint", 4),
                ("KeyHandle", "uint", 8),
                ("KeyName", "utf16", None),
            ),
        )
        for opcode, operation in _REGISTRY_OPERATION_BY_OPCODE.items()
    ),
)


def _kernel_partition_is_complete() -> bool:
    simple_file = (
        ("IrpPtr", "uint", 8),
        ("TTID", "pointer", 8),
        ("FileObject", "uint", 8),
        ("FileKey", "uint", 8),
    )
    expected: dict[
        tuple[uuid.UUID, int, int],
        tuple[
            str,
            str,
            tuple[tuple[str, str, int | None], ...],
            str,
        ],
    ] = {}
    for opcode in _FILE_NAME_OPCODES:
        expected[(_FILE_PROVIDER_UUID, opcode, 2)] = (
            "file_access",
            "file_name",
            (("FileObject", "uint", 8), ("FileName", "utf16", None)),
            "full_payload",
        )
    expected[(_FILE_PROVIDER_UUID, 64, 2)] = (
        "file_access",
        "file_create",
        (
            ("IrpPtr", "uint", 8),
            ("TTID", "pointer", 8),
            ("FileObject", "uint", 8),
            ("CreateOptions", "uint", 4),
            ("FileAttributes", "uint", 4),
            ("ShareAccess", "uint", 4),
            ("OpenPath", "utf16", None),
        ),
        "full_payload",
    )
    for opcode in (65, 66, 73):
        expected[(_FILE_PROVIDER_UUID, opcode, 2)] = (
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            simple_file,
            "full_payload",
        )
    for opcode in (67, 68):
        expected[(_FILE_PROVIDER_UUID, opcode, 2)] = (
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("Offset", "uint", 8),
                *simple_file,
                ("IoSize", "uint", 4),
                ("IoFlags", "uint", 4),
            ),
            "full_payload",
        )
    for opcode in (69, 70, 71, 74, 75):
        expected[(_FILE_PROVIDER_UUID, opcode, 2)] = (
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (*simple_file, ("ExtraInfo", "uint", 8), ("InfoClass", "uint", 4)),
            "full_payload",
        )
    for opcode in (72, 77):
        expected[(_FILE_PROVIDER_UUID, opcode, 2)] = (
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                *simple_file,
                ("Length", "uint", 4),
                ("InfoClass", "uint", 4),
                ("FileIndex", "uint", 4),
                ("FileName", "utf16", None),
            ),
            "full_payload",
        )
    expected[(_FILE_PROVIDER_UUID, 76, 2)] = (
        "file_access",
        "file_operation_end",
        (
            ("IrpPtr", "uint", 8),
            ("ExtraInfo", "uint", 8),
            ("NtStatus", "uint", 4),
        ),
        "full_payload",
    )
    for opcode in _FILE_NAME_OPCODES:
        expected[(_FILE_PROVIDER_UUID, opcode, 3)] = (
            "file_access",
            "file_name",
            (("FileObject", "pointer", 8), ("FileName", "utf16", None)),
            "full_payload",
        )
    expected[(_FILE_PROVIDER_UUID, 64, 3)] = (
        "file_access",
        "file_create",
        (
            ("IrpPtr", "pointer", 8),
            ("FileObject", "pointer", 8),
            ("TTID", "uint", 4),
            ("CreateOptions", "uint", 4),
            ("FileAttributes", "uint", 4),
            ("ShareAccess", "uint", 4),
            ("OpenPath", "utf16", None),
        ),
        "full_payload",
    )
    simple_file_v3 = (
        ("IrpPtr", "pointer", 8),
        ("FileObject", "pointer", 8),
        ("FileKey", "pointer", 8),
        ("TTID", "uint", 4),
    )
    for opcode in (65, 66, 73):
        expected[(_FILE_PROVIDER_UUID, opcode, 3)] = (
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            simple_file_v3,
            "full_payload",
        )
    for opcode in (67, 68):
        expected[(_FILE_PROVIDER_UUID, opcode, 3)] = (
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("Offset", "uint", 8),
                *simple_file_v3,
                ("IoSize", "uint", 4),
                ("IoFlags", "uint", 4),
            ),
            "full_payload",
        )
    for opcode in (69, 70, 71, 74, 75):
        expected[(_FILE_PROVIDER_UUID, opcode, 3)] = (
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                ("IrpPtr", "pointer", 8),
                ("FileObject", "pointer", 8),
                ("FileKey", "pointer", 8),
                ("ExtraInfo", "pointer", 8),
                ("TTID", "uint", 4),
                ("InfoClass", "uint", 4),
            ),
            "full_payload",
        )
    for opcode in (72, 77):
        expected[(_FILE_PROVIDER_UUID, opcode, 3)] = (
            "file_access",
            _FILE_OPERATION_BY_OPCODE[opcode],
            (
                *simple_file_v3,
                ("Length", "uint", 4),
                ("InfoClass", "uint", 4),
                ("FileIndex", "uint", 4),
                ("FileName", "utf16", None),
            ),
            "full_payload",
        )
    expected[(_FILE_PROVIDER_UUID, 76, 3)] = (
        "file_access",
        "file_operation_end",
        (
            ("IrpPtr", "pointer", 8),
            ("ExtraInfo", "pointer", 8),
            ("NtStatus", "uint", 4),
        ),
        "full_payload",
    )
    for opcode in _THREAD_OPCODES:
        expected[(_THREAD_PROVIDER_UUID, opcode, 3)] = (
            "file_access",
            "thread_lifecycle",
            _THREAD_V3_FIELDS,
            "consumed_attribution_subset",
        )
    expected[(_IMAGE_PROVIDER_UUID, 10, 2)] = (
        "dll_image_load",
        "image_map",
        (("ProcessId", "uint", 4), ("FileName", "utf16", None)),
        "consumed_attribution_subset",
    )
    registry_fields = (
        ("InitialTime", "sint", 8),
        ("Status", "uint", 4),
        ("Index", "uint", 4),
        ("KeyHandle", "uint", 8),
        ("KeyName", "utf16", None),
    )
    for opcode in range(10, 28):
        expected[(_REGISTRY_PROVIDER_UUID, opcode, 2)] = (
            "registry_access",
            _REGISTRY_OPERATION_BY_OPCODE[opcode],
            registry_fields,
            "full_payload",
        )
    actual_keys = tuple(
        (item.provider, item.opcode, item.version)
        for item in _KERNEL_EVENT_TEMPLATES
    )
    if len(actual_keys) != len(set(actual_keys)):
        return False
    actual = {
        (item.provider, item.opcode, item.version): (
            item.plane,
            item.operation,
            item.fields,
            item.field_contract,
        )
        for item in _KERNEL_EVENT_TEMPLATES
    }
    return bool(ctypes.sizeof(ctypes.c_void_p) == 8 and actual == expected)


def _kernel_template(
    provider: bytes, opcode: int, version: int
) -> _KernelEventTemplate | None:
    for template in _KERNEL_EVENT_TEMPLATES:
        if (
            _GUID.from_uuid(template.provider).key() == provider
            and template.opcode == opcode
            and template.version == version
        ):
            return template
    return None


@dataclass(frozen=True, slots=True)
class _ManifestProvider:
    provider: uuid.UUID
    any_keyword: int
    plane: str
    descriptor_count: int
    descriptor_digest: str


@dataclass(frozen=True, slots=True)
class _ManifestField:
    name: str
    kind: str
    width: int | None


@dataclass(frozen=True, slots=True)
class _ManifestEventTemplate:
    provider: uuid.UUID
    event_id: int
    version: int
    fields: tuple[_ManifestField, ...]
    process_field: str | None
    object_field: str | None
    status_field: str | None
    plane: str
    semantic: str


@dataclass(frozen=True, slots=True)
class _ManifestCapability:
    provider: uuid.UUID
    plane: str
    required_events: tuple[tuple[int, int], ...]
    closure_frozen: bool


_MANIFEST_PROVIDERS = (
    _ManifestProvider(
        _USER_LOADER_PROVIDER_UUID,
        0x40 | 0x200 | 0x2000000000000000 | 0x8000000000000000,
        "dll_image_load",
        12,
        "b83c0f51b04fd4a2ca1536755e871ca45a2d4cf7bb3f9fcaf0268852f04eb411",
    ),
    _ManifestProvider(
        _CI_PROVIDER_UUID,
        0x8000000000000000 | 0x4000000000000000,
        "code_integrity_policy",
        185,
        "838156db3a1d893d43e1cfd39d7908603ad38a5e33077cc0c1db2d753c6a55ea",
    ),
)

def _mf(name: str, kind: str, width: int | None = None) -> _ManifestField:
    return _ManifestField(name, kind, width)


def _ul(
    event_id: int,
    fields: tuple[_ManifestField, ...],
    *,
    semantic: str,
    process_field: str | None = None,
    object_field: str | None = None,
    status_field: str | None = None,
) -> _ManifestEventTemplate:
    return _ManifestEventTemplate(
        _USER_LOADER_PROVIDER_UUID,
        event_id,
        0,
        fields,
        process_field,
        object_field,
        status_field,
        "dll_image_load",
        semantic,
    )


_USER_LOADER_TEMPLATES = (
    _ul(1, (_mf("FileName", "utf16"),), semantic="ancillary", object_field="FileName"),
    _ul(
        2,
        (
            _mf("ProcessFileNamePathLength", "uint", 2),
            _mf("ProcessFileNamePath", "utf16"),
        ),
        semantic="fatal",
        process_field="ProcessFileNamePath",
    ),
    _ul(
        3,
        (
            _mf("FailureReason", "uint32", 4),
            _mf("ImportDllName", "utf16"),
            _mf("ProcessImagePath", "utf16"),
        ),
        semantic="status_denial",
        process_field="ProcessImagePath",
        object_field="ImportDllName",
        status_field="FailureReason",
    ),
    _ul(4, (_mf("FileName", "utf16"),), semantic="ancillary", process_field="FileName"),
    _ul(
        5,
        (
            _mf("ProcessId", "uint32", 4),
            _mf("SuspendProcessRequest", "uint32", 4),
            _mf("DLLName", "utf16"),
        ),
        semantic="ancillary",
        object_field="DLLName",
    ),
    *(
        _ul(
            event_id,
            (_mf("FileName", "utf16"),),
            semantic="fatal",
            process_field="FileName",
        )
        for event_id in (6, 7)
    ),
    _ul(
        8,
        (
            _mf("FailureReason", "uint32", 4),
            _mf("ImportDllName", "utf16"),
            _mf("ExportModule", "utf16"),
        ),
        semantic="status_denial",
        object_field="ImportDllName",
        status_field="FailureReason",
    ),
    _ul(9, (_mf("FileName", "utf16"),), semantic="fatal", process_field="FileName"),
    _ul(
        10,
        (
            _mf("FailureReason", "uint32", 4),
            _mf("ImportDllName", "utf16"),
            _mf("ProcessImagePath", "utf16"),
        ),
        semantic="status_denial",
        process_field="ProcessImagePath",
        object_field="ImportDllName",
        status_field="FailureReason",
    ),
    _ul(
        11,
        (
            _mf("ProcessImagePath", "utf16"),
            _mf("CurDirDllPath", "utf16"),
            _mf("FoundDllPath", "utf16"),
        ),
        semantic="ancillary",
        process_field="ProcessImagePath",
        object_field="FoundDllPath",
    ),
    _ul(
        12,
        (
            _mf("ProcessImagePath", "utf16"),
            _mf("CurDirDllPath", "utf16"),
        ),
        semantic="fatal",
        process_field="ProcessImagePath",
        object_field="CurDirDllPath",
    ),
)


def _ci(
    event_id: int,
    version: int,
    *,
    semantic: str,
    file_field: str | None,
    process_field: str | None = None,
    status_field: str | None = None,
    status_kind: str = "hexint32",
) -> _ManifestEventTemplate:
    fields: list[_ManifestField] = []
    if file_field is not None:
        fields.append(_mf(file_field, "utf16"))
    if process_field is not None:
        fields.append(_mf(process_field, "utf16"))
    if status_field is not None:
        fields.append(_mf(status_field, status_kind, 4))
    return _ManifestEventTemplate(
        _CI_PROVIDER_UUID,
        event_id,
        version,
        tuple(fields),
        process_field,
        file_field,
        status_field,
        "code_integrity_policy",
        semantic,
    )


_CI_DENIAL_TEMPLATES = (
    _ci(3004, 1, semantic="denial", file_field="FileNameBuffer", process_field="ProcessNameBuffer"),
    _ci(3033, 0, semantic="denial", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status", status_kind="uint32"),
    _ci(3063, 0, semantic="denial", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status"),
    _ci(3068, 0, semantic="denial", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status", status_kind="uint32"),
    *(
        _ci(3077, version, semantic="denial", file_field="File Name", process_field="Process Name", status_field="Status", status_kind="uint32" if version <= 2 else "hexint32")
        for version in range(6)
    ),
    *(
        _ci(3079, version, semantic="denial", file_field="File Name", process_field="Process Name", status_field="Status", status_kind="uint32" if version <= 2 else "hexint32")
        for version in range(4)
    ),
    *(
        _ci(3081, version, semantic="denial", file_field="File Name", process_field="Process Name", status_field="Status", status_kind="uint32" if version <= 8 else "hexint32")
        for version in range(13)
    ),
    _ci(3086, 0, semantic="denial", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status", status_kind="uint32"),
    _ci(3111, 0, semantic="denial", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status"),
    _ci(3119, 0, semantic="denial", file_field="File Name", process_field="Process Name", status_field="Status"),
)

_CI_FATAL_TEMPLATES = (
    *(
        _ci(event_id, version, semantic="fatal", file_field="FileNameBuffer", process_field="ProcessNameBuffer" if version == 1 else None)
        for event_id in (3002, 3023, 3036)
        for version in (0, 1)
    ),
    _ci(3004, 0, semantic="fatal", file_field="FileNameBuffer"),
    _ci(3010, 0, semantic="fatal", file_field="FileNameBuffer"),
    _ci(3010, 1, semantic="fatal", file_field="FileNameBuffer", status_field="Status"),
    _ci(3026, 0, semantic="fatal", file_field="FileNameBuffer"),
    _ci(3072, 0, semantic="fatal", file_field="FileNameBuffer"),
    _ci(3073, 0, semantic="fatal", file_field="FileNameBuffer"),
    _ci(3074, 0, semantic="global_fatal", file_field=None, status_field="Status"),
    _ci(3087, 0, semantic="fatal", file_field="FileNameBuffer", status_field="Status"),
    _ci(3087, 1, semantic="fatal", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status"),
    *(
        _ci(event_id, 0, semantic="fatal", file_field="FileNameBuffer", status_field="Status" if event_id in {3106, 3107} else None)
        for event_id in (3104, 3106, 3107)
    ),
    *(
        _ci(3092, version, semantic="fatal", file_field="FileName", status_field="StatusCode")
        for version in (0, 1)
    ),
    _ci(3114, 0, semantic="fatal", file_field="FileName", process_field="ProcessName", status_field="Status"),
    _ci(3118, 0, semantic="fatal", file_field="FileNameBuffer", status_field="DefenderStatusCode"),
)

_CI_AUDIT_TEMPLATES = (
    *(
        _ci(event_id, version, semantic="audit", file_field="FileNameBuffer", process_field="ProcessNameBuffer" if version == 1 else None)
        for event_id in (3001, 3032)
        for version in (0, 1)
    ),
    _ci(3034, 0, semantic="audit", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status", status_kind="uint32"),
    *(
        _ci(event_id, 0, semantic="audit", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status", status_kind="hexint32" if event_id in {3064, 3065} else "uint32")
        for event_id in (3064, 3065, 3066, 3067)
    ),
    *(
        _ci(3076, version, semantic="audit", file_field="File Name", process_field="Process Name", status_field="Status", status_kind="uint32" if version <= 2 else "hexint32")
        for version in range(6)
    ),
    *(
        _ci(3078, version, semantic="audit", file_field="File Name", process_field="Process Name", status_field="Status", status_kind="uint32" if version <= 2 else "hexint32")
        for version in range(4)
    ),
    *(
        _ci(3080, version, semantic="audit", file_field="File Name", process_field="Process Name", status_field="Status", status_kind="uint32" if version <= 8 else "hexint32")
        for version in range(13)
    ),
    _ci(3082, 0, semantic="audit", file_field="FileNameBuffer"),
    *(
        _ci(event_id, version, semantic="audit", file_field="FileName", status_field="StatusCode")
        for event_id, versions in ((3088, (0,)), (3090, (0,)), (3091, (0, 1)))
        for version in versions
    ),
    *(
        _ci(3089, version, semantic="audit", file_field=None)
        for version in range(4)
    ),
    _ci(3112, 0, semantic="audit", file_field="FileNameBuffer", process_field="ProcessNameBuffer", status_field="Status", status_kind="uint32"),
    _ci(3115, 0, semantic="audit", file_field="FileName", process_field="ProcessName", status_field="Status"),
    _ci(3117, 0, semantic="audit", file_field="File Name", process_field="Process Name"),
)

_MANIFEST_TEMPLATES: tuple[_ManifestEventTemplate, ...] = (
    *_USER_LOADER_TEMPLATES,
    *_CI_DENIAL_TEMPLATES,
    *_CI_FATAL_TEMPLATES,
    *_CI_AUDIT_TEMPLATES,
)


def _manifest_template(
    provider: bytes, event_id: int, version: int
) -> _ManifestEventTemplate | None:
    for template in _MANIFEST_TEMPLATES:
        if (
            _GUID.from_uuid(template.provider).key() == provider
            and template.event_id == event_id
            and template.version == version
        ):
            return template
    return None


_MANIFEST_CAPABILITIES = tuple(
    _ManifestCapability(
        provider.provider,
        provider.plane,
        tuple(
            (item.event_id, item.version)
            for item in _MANIFEST_TEMPLATES
            if item.provider == provider.provider and item.plane == provider.plane
        ),
        True,
    )
    for provider in _MANIFEST_PROVIDERS
)


def _provider_descriptor_digest(
    provider: uuid.UUID,
    descriptors: tuple[tuple[int, int, int, int, int, int, int], ...],
) -> str | None:
    if (
        type(provider) is not uuid.UUID
        or type(descriptors) is not tuple
        or not descriptors
        or len(set(descriptors)) != len(descriptors)
        or any(
            type(item) is not tuple
            or len(item) != 7
            or any(type(value) is not int or value < 0 for value in item)
            for item in descriptors
        )
    ):
        return None
    canonical = hashlib.sha256()
    canonical.update(b"taskgov-m241b-provider-descriptor-v1\0")
    canonical.update(str(provider).encode("ascii"))
    canonical.update(b"\0")
    canonical.update(str(len(descriptors)).encode("ascii"))
    canonical.update(b"\0")
    for item in sorted(descriptors):
        canonical.update(
            (
                f"{item[0]}:{item[1]}:{item[2]}:{item[3]}:"
                f"{item[4]}:{item[5]}:{item[6]:016x}\n"
            ).encode("ascii")
        )
    return canonical.hexdigest()


class _WindowsEtwPort:
    def __init__(self, reducer: _TraceReducer) -> None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            _fail("trace_unavailable")
        self._reducer = reducer
        self._advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._tdh = ctypes.WinDLL("tdh", use_last_error=True)
        self._logger_buffer: ctypes.Array[ctypes.c_wchar] | None = None
        self._logfile: _EVENT_TRACE_LOGFILEW | None = None
        self._record_cb: object | None = None
        self._buffer_cb: object | None = None
        self._configure_apis()

    def _configure_apis(self) -> None:
        a = self._advapi
        k = self._kernel
        a.StartTraceW.argtypes = (
            ctypes.POINTER(TRACEHANDLE), wintypes.LPCWSTR, ctypes.POINTER(_EVENT_TRACE_PROPERTIES)
        )
        a.StartTraceW.restype = ULONG
        a.ControlTraceW.argtypes = (
            TRACEHANDLE, wintypes.LPCWSTR, ctypes.POINTER(_EVENT_TRACE_PROPERTIES), ULONG
        )
        a.ControlTraceW.restype = ULONG
        a.OpenTraceW.argtypes = (ctypes.POINTER(_EVENT_TRACE_LOGFILEW),)
        a.OpenTraceW.restype = TRACEHANDLE
        a.ProcessTrace.argtypes = (
            ctypes.POINTER(TRACEHANDLE), ULONG, ctypes.c_void_p, ctypes.c_void_p
        )
        a.ProcessTrace.restype = ULONG
        a.CloseTrace.argtypes = (TRACEHANDLE,)
        a.CloseTrace.restype = ULONG
        a.EnableTraceEx2.argtypes = (
            TRACEHANDLE,
            ctypes.POINTER(_GUID),
            ULONG,
            UCHAR,
            ULONGLONG,
            ULONGLONG,
            ULONG,
            ctypes.c_void_p,
        )
        a.EnableTraceEx2.restype = ULONG
        self._tdh.TdhGetPropertySize.argtypes = (
            ctypes.POINTER(_EVENT_RECORD),
            ULONG,
            ctypes.c_void_p,
            ULONG,
            ctypes.POINTER(_PROPERTY_DATA_DESCRIPTOR),
            ctypes.POINTER(ULONG),
        )
        self._tdh.TdhGetPropertySize.restype = ULONG
        self._tdh.TdhGetProperty.argtypes = (
            ctypes.POINTER(_EVENT_RECORD),
            ULONG,
            ctypes.c_void_p,
            ULONG,
            ctypes.POINTER(_PROPERTY_DATA_DESCRIPTOR),
            ULONG,
            ctypes.c_void_p,
        )
        self._tdh.TdhGetProperty.restype = ULONG
        self._tdh.TdhEnumerateManifestProviderEvents.argtypes = (
            ctypes.POINTER(_GUID),
            ctypes.c_void_p,
            ctypes.POINTER(ULONG),
        )
        self._tdh.TdhEnumerateManifestProviderEvents.restype = ULONG
        k.QueryPerformanceCounter.argtypes = (ctypes.POINTER(LONGLONG),)
        k.QueryPerformanceCounter.restype = wintypes.BOOL
        k.GetProcessId.argtypes = (ctypes.c_void_p,)
        k.GetProcessId.restype = ULONG
        k.GetThreadId.argtypes = (ctypes.c_void_p,)
        k.GetThreadId.restype = ULONG
        k.GetProcessIdOfThread.argtypes = (ctypes.c_void_p,)
        k.GetProcessIdOfThread.restype = ULONG

    @staticmethod
    def _properties() -> tuple[ctypes.Array[ctypes.c_char], _EVENT_TRACE_PROPERTIES]:
        name_bytes = (len(SESSION_NAME) + 1) * ctypes.sizeof(wintypes.WCHAR)
        raw = ctypes.create_string_buffer(ctypes.sizeof(_EVENT_TRACE_PROPERTIES) + name_bytes)
        props = _EVENT_TRACE_PROPERTIES.from_buffer(raw)
        props.Wnode.BufferSize = len(raw)
        props.Wnode.Guid = _GUID.from_uuid(SESSION_GUID)
        props.Wnode.ClientContext = 1
        props.Wnode.Flags = _WNODE_FLAG_TRACED_GUID
        props.BufferSize = 64
        props.MinimumBuffers = 4
        props.MaximumBuffers = 64
        props.LogFileMode = EXACT_LOG_FILE_MODE
        props.FlushTimer = 1
        props.EnableFlags = EXACT_KERNEL_ENABLE_FLAGS
        props.LogFileNameOffset = 0
        props.LoggerNameOffset = ctypes.sizeof(_EVENT_TRACE_PROPERTIES)
        name = ctypes.create_unicode_buffer(SESSION_NAME)
        ctypes.memmove(ctypes.addressof(raw) + props.LoggerNameOffset, name, name_bytes)
        return raw, props

    def process_id(self, process_handle: int) -> int:
        return int(self._kernel.GetProcessId(ctypes.c_void_p(process_handle)))

    def thread_binding(
        self, process_handle: int, thread_handle: int
    ) -> tuple[int, int]:
        if self.process_id(process_handle) <= 0:
            return 0, 0
        return (
            int(self._kernel.GetProcessIdOfThread(ctypes.c_void_p(thread_handle))),
            int(self._kernel.GetThreadId(ctypes.c_void_p(thread_handle))),
        )

    def qpc(self) -> int:
        value = LONGLONG()
        if not self._kernel.QueryPerformanceCounter(ctypes.byref(value)):
            _fail("trace_unavailable")
        return int(value.value)

    def session_present(self) -> bool:
        raw, props = self._query_properties()
        status = int(
            self._advapi.ControlTraceW(
                TRACEHANDLE(0), SESSION_NAME, ctypes.byref(props), _EVENT_TRACE_CONTROL_QUERY
            )
        )
        # `props` is a from_buffer view; keep `raw` strongly referenced until
        # ControlTraceW has returned.
        _ = raw
        if status == _ERROR_SUCCESS:
            return True
        if status == _ERROR_WMI_INSTANCE_NOT_FOUND:
            return False
        _fail("trace_unavailable")

    def start_session(self) -> int:
        raw, props = self._properties()
        handle = TRACEHANDLE()
        status = int(self._advapi.StartTraceW(ctypes.byref(handle), SESSION_NAME, ctypes.byref(props)))
        # `props` is a from_buffer view; keep `raw` strongly referenced until
        # StartTraceW has returned.
        _ = raw
        if status in {_ERROR_ALREADY_EXISTS}:
            _fail("trace_session_collision")
        if status != _ERROR_SUCCESS or int(handle.value) == 0:
            _fail("trace_unavailable")
        return int(handle.value)

    @staticmethod
    def _query_properties(
    ) -> tuple[ctypes.Array[ctypes.c_char], _EVENT_TRACE_PROPERTIES]:
        name_bytes = (len(SESSION_NAME) + 1) * ctypes.sizeof(wintypes.WCHAR)
        raw = ctypes.create_string_buffer(
            ctypes.sizeof(_EVENT_TRACE_PROPERTIES) + name_bytes
        )
        props = _EVENT_TRACE_PROPERTIES.from_buffer(raw)
        # QUERY/STOP inputs are fresh zeroed buffers.  API success plus the
        # exact expected nonzero mode/flags/context is the overwrite proof;
        # invalid input canaries would themselves make ControlTrace reject.
        props.Wnode.BufferSize = len(raw)
        props.Wnode.Guid = _GUID.from_uuid(SESSION_GUID)
        props.LoggerNameOffset = ctypes.sizeof(_EVENT_TRACE_PROPERTIES)
        name = ctypes.create_unicode_buffer(SESSION_NAME)
        ctypes.memmove(
            ctypes.addressof(raw) + props.LoggerNameOffset,
            name,
            name_bytes,
        )
        return raw, props

    def query_session(
        self, owned_session_handle: int
    ) -> KernelSessionSnapshot:
        if type(owned_session_handle) is not int or owned_session_handle <= 0:
            _fail("trace_unavailable")
        raw, props = self._query_properties()
        status = int(
            self._advapi.ControlTraceW(
                TRACEHANDLE(owned_session_handle),
                SESSION_NAME,
                ctypes.byref(props),
                _EVENT_TRACE_CONTROL_QUERY,
            )
        )
        _ = raw
        if status != _ERROR_SUCCESS:
            _fail("trace_unavailable")
        return KernelSessionSnapshot(
            int(props.EnableFlags),
            int(props.LogFileMode),
            int(props.Wnode.ClientContext),
            int(props.EventsLost),
            int(props.LogBuffersLost),
            int(props.RealTimeBuffersLost),
        )

    def _provider_descriptor_binding(
        self, provider: _ManifestProvider
    ) -> str | None:
        """Prove the complete registered provider descriptor set in memory."""

        guid = _GUID.from_uuid(provider.provider)
        size = ULONG()
        try:
            status = int(
                self._tdh.TdhEnumerateManifestProviderEvents(
                    ctypes.byref(guid), None, ctypes.byref(size)
                )
            )
        except BaseException:
            return None
        if (
            status != _ERROR_INSUFFICIENT_BUFFER
            or not ctypes.sizeof(_PROVIDER_EVENT_INFO_HEADER)
            <= int(size.value)
            <= MAX_PROVIDER_DESCRIPTOR_BYTES
        ):
            return None
        raw = ctypes.create_string_buffer(int(size.value))
        allocated = len(raw)
        try:
            status = int(
                self._tdh.TdhEnumerateManifestProviderEvents(
                    ctypes.byref(guid),
                    ctypes.cast(raw, ctypes.c_void_p),
                    ctypes.byref(size),
                )
            )
        except BaseException:
            return None
        if status != _ERROR_SUCCESS or int(size.value) > allocated:
            return None
        header = _PROVIDER_EVENT_INFO_HEADER.from_buffer(raw)
        count = int(header.NumberOfEvents)
        required = ctypes.sizeof(_PROVIDER_EVENT_INFO_HEADER) + (
            count * ctypes.sizeof(_EVENT_DESCRIPTOR)
        )
        if (
            int(header.Reserved) != 0
            or count != provider.descriptor_count
            or not 1 <= count <= MAX_PROVIDER_DESCRIPTORS
            or required > int(size.value)
        ):
            return None
        descriptors: list[tuple[int, int, int, int, int, int, int]] = []
        offset = ctypes.sizeof(_PROVIDER_EVENT_INFO_HEADER)
        for index in range(count):
            item = _EVENT_DESCRIPTOR.from_buffer_copy(
                raw,
                offset + index * ctypes.sizeof(_EVENT_DESCRIPTOR),
            )
            descriptors.append(
                (
                    int(item.Id),
                    int(item.Version),
                    int(item.Channel),
                    int(item.Level),
                    int(item.Opcode),
                    int(item.Task),
                    int(item.Keyword),
                )
            )
        if len(set(descriptors)) != count:
            return None
        digest = _provider_descriptor_digest(
            provider.provider, tuple(descriptors)
        )
        if digest != provider.descriptor_digest:
            return None
        return "provider-sha256:" + digest

    def enable_session(self, owned_session_handle: int) -> frozenset[str]:
        if type(owned_session_handle) is not int or owned_session_handle <= 0:
            _fail("trace_unavailable")
        handle = TRACEHANDLE(owned_session_handle)
        capable: set[str] = set()
        if _kernel_partition_is_complete():
            capable.update({"file_access", "registry_access"})
        for provider in _MANIFEST_PROVIDERS:
            descriptor_binding = self._provider_descriptor_binding(provider)
            guid = _GUID.from_uuid(provider.provider)
            enabled = int(
                self._advapi.EnableTraceEx2(
                    handle,
                    ctypes.byref(guid),
                    _EVENT_CONTROL_CODE_ENABLE_PROVIDER,
                    _TRACE_LEVEL_VERBOSE,
                    ULONGLONG(provider.any_keyword),
                    ULONGLONG(0),
                    0,
                    None,
                )
            )
            capability = next(
                (
                    item
                    for item in _MANIFEST_CAPABILITIES
                    if item.provider == provider.provider
                    and item.plane == provider.plane
                ),
                None,
            )
            template_keys = {
                (item.event_id, item.version)
                for item in _MANIFEST_TEMPLATES
                if item.provider == provider.provider and item.plane == provider.plane
            }
            if (
                enabled == _ERROR_SUCCESS
                and capability is not None
                and capability.closure_frozen
                and capability.required_events
                and len(capability.required_events) == len(template_keys)
                and set(capability.required_events) == template_keys
                and descriptor_binding is not None
                and any(
                    item.provider == provider.provider
                    and item.plane == provider.plane
                    and item.semantic == "denial"
                    and item.fields
                    and item.object_field is not None
                    for item in _MANIFEST_TEMPLATES
                )
            ):
                self._reducer.bind_manifest_identity(
                    provider.plane, descriptor_binding
                )
                capable.add(provider.plane)
        return frozenset(capable)

    def open_consumer(
        self,
        record_callback: Callable[[object], None],
        loss_callback: Callable[[], None],
    ) -> int:
        self._logger_buffer = ctypes.create_unicode_buffer(SESSION_NAME)

        def on_record(pointer: ctypes.POINTER(_EVENT_RECORD)) -> None:
            try:
                record_callback(pointer)
            except BaseException:
                self._reducer.mark_schema_unknown("unknown")

        def on_buffer(pointer: ctypes.POINTER(_EVENT_TRACE_LOGFILEW)) -> int:
            try:
                if pointer and (
                    int(pointer.contents.EventsLost)
                    or int(pointer.contents.LogfileHeader.BuffersLost)
                ):
                    loss_callback()
            except BaseException:
                loss_callback()
            return 1

        self._record_cb = _EVENT_RECORD_CALLBACK(on_record)
        self._buffer_cb = _BUFFER_CALLBACK(on_buffer)
        logfile = _EVENT_TRACE_LOGFILEW()
        logfile.LoggerName = ctypes.cast(self._logger_buffer, wintypes.LPWSTR)
        # Wnode.ClientContext=1 selects QPC.  RAW_TIMESTAMP is mandatory so
        # ProcessTrace does not convert EventHeader.TimeStamp to system time.
        logfile.ProcessTraceMode = EXACT_PROCESS_TRACE_MODE
        logfile.EventRecordCallback = self._record_cb
        logfile.BufferCallback = self._buffer_cb
        self._logfile = logfile
        handle = int(self._advapi.OpenTraceW(ctypes.byref(logfile)))
        if handle == _INVALID_PROCESSTRACE_HANDLE:
            _fail("trace_unavailable")
        return handle

    def process(self, trace_handle: int) -> int:
        handle = TRACEHANDLE(trace_handle)
        return int(self._advapi.ProcessTrace(ctypes.byref(handle), 1, None, None))

    def stop_session(self, owned_session_handle: int) -> StopTraceResult:
        raw, props = self._query_properties()
        stopped = int(
            self._advapi.ControlTraceW(
                TRACEHANDLE(owned_session_handle),
                SESSION_NAME,
                ctypes.byref(props),
                _EVENT_TRACE_CONTROL_STOP,
            )
        ) == _ERROR_SUCCESS
        # `props` is a from_buffer view; keep `raw` strongly referenced until
        # ControlTraceW has returned.
        _ = raw
        snapshot = KernelSessionSnapshot(
            int(props.EnableFlags),
            int(props.LogFileMode),
            int(props.Wnode.ClientContext),
            int(props.EventsLost),
            int(props.LogBuffersLost),
            int(props.RealTimeBuffersLost),
        )
        return StopTraceResult(
            stopped,
            int(props.EventsLost),
            int(props.LogBuffersLost),
            int(props.RealTimeBuffersLost),
            snapshot,
        )

    def close_consumer(self, trace_handle: int) -> bool:
        return int(self._advapi.CloseTrace(TRACEHANDLE(trace_handle))) == _ERROR_SUCCESS

    def _property(
        self, record: ctypes.POINTER(_EVENT_RECORD), name: str
    ) -> bytes | None:
        name_buffer = ctypes.create_unicode_buffer(name)
        descriptor = _PROPERTY_DATA_DESCRIPTOR(
            ctypes.addressof(name_buffer), 0xFFFFFFFF, 0
        )
        size = ULONG()
        status = int(
            self._tdh.TdhGetPropertySize(
                record, 0, None, 1, ctypes.byref(descriptor), ctypes.byref(size)
            )
        )
        if status != _ERROR_SUCCESS or not 1 <= size.value <= MAX_PROPERTY_BYTES:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        status = int(
            self._tdh.TdhGetProperty(
                record,
                0,
                None,
                1,
                ctypes.byref(descriptor),
                size.value,
                buffer,
            )
        )
        if status != _ERROR_SUCCESS:
            return None
        return bytes(buffer.raw)

    def _integer(
        self,
        record: ctypes.POINTER(_EVENT_RECORD),
        name: str,
        width: int,
    ) -> int | None:
        raw = self._property(record, name)
        if raw is None or len(raw) != width:
            return None
        return int.from_bytes(raw, "little", signed=False)

    def _signed_integer(
        self,
        record: ctypes.POINTER(_EVENT_RECORD),
        name: str,
        width: int,
    ) -> int | None:
        raw = self._property(record, name)
        if raw is None or len(raw) != width:
            return None
        return int.from_bytes(raw, "little", signed=True)

    def _string(
        self,
        record: ctypes.POINTER(_EVENT_RECORD),
        name: str,
        *,
        allow_empty: bool = False,
    ) -> str | None:
        raw = self._property(record, name)
        if raw is None or len(raw) % 2:
            return None
        try:
            value = raw.decode("utf-16-le", errors="strict").rstrip("\0")
        except UnicodeError:
            return None
        if (not value and not allow_empty) or "\0" in value:
            return None
        return value

    def _kernel_values(
        self,
        record: ctypes.POINTER(_EVENT_RECORD),
        template: _KernelEventTemplate,
        *,
        allow_empty_strings: frozenset[str] = frozenset(),
    ) -> dict[str, int | str] | None:
        values: dict[str, int | str] = {}
        for name, kind, width in template.fields:
            if kind == "utf16" and width is None:
                value: int | str | None = self._string(
                    record,
                    name,
                    allow_empty=name in allow_empty_strings,
                )
            elif kind in {"uint", "pointer"} and width is not None:
                value = self._integer(record, name, width)
            elif kind == "sint" and width is not None:
                value = self._signed_integer(record, name, width)
            else:
                return None
            if value is None:
                return None
            values[name] = value
        return values

    def _manifest_values(
        self,
        record: ctypes.POINTER(_EVENT_RECORD),
        template: _ManifestEventTemplate,
    ) -> dict[str, int | str] | None:
        values: dict[str, int | str] = {}
        for field in template.fields:
            if field.kind == "utf16" and field.width is None:
                value: int | str | None = self._string(record, field.name)
            elif field.kind in {"uint", "uint32", "hexint32"} and field.width:
                value = self._integer(record, field.name, field.width)
            else:
                return None
            if value is None:
                return None
            values[field.name] = value
        return values

    @staticmethod
    def _classic_header_is_exact(header: _EVENT_HEADER) -> bool:
        flags = int(header.Flags)
        return bool(
            int(header.EventDescriptor.Id) == _CLASSIC_EVENT_ID
            and flags & _EVENT_HEADER_FLAG_CLASSIC_HEADER
            and flags & _EVENT_HEADER_FLAG_64_BIT_HEADER
            and not flags & _EVENT_HEADER_FLAG_32_BIT_HEADER
        )

    def translate(self, opaque_record: object) -> None:
        if not isinstance(opaque_record, ctypes._Pointer):  # type: ignore[attr-defined]
            self._reducer.mark_schema_unknown("unknown")
            return
        record = ctypes.cast(opaque_record, ctypes.POINTER(_EVENT_RECORD))
        header = record.contents.EventHeader
        if not self._reducer.begin_callback():
            return
        provider = header.ProviderId.key()
        opcode = int(header.EventDescriptor.Opcode)
        event_id = int(header.EventDescriptor.Id)
        version = int(header.EventDescriptor.Version)
        pid = int(header.ProcessId)
        timestamp = int(header.TimeStamp)

        target_pid = self._reducer._pid
        if (
            self._reducer._primary_thread_id is not None
            and provider in {_THREAD_PROVIDER, _FILE_PROVIDER, _REGISTRY_PROVIDER}
            and self._reducer._kernel_setup_or_window(timestamp)
            and not self._classic_header_is_exact(header)
        ):
            self._reducer.mark_schema_unknown(
                "registry_access"
                if provider == _REGISTRY_PROVIDER
                else "file_access"
            )
            return
        if provider == _THREAD_PROVIDER:
            if not self._reducer._kernel_setup_or_window(timestamp):
                return
            template = _kernel_template(provider, opcode, version)
            if template is None:
                self._reducer.mark_unknown_thread_event(timestamp=timestamp)
                return
            if not self._reducer.inspect_payload(
                int(record.contents.UserDataLength)
            ):
                return
            values = self._kernel_values(record, template)
            if values is None:
                self._reducer.mark_unknown_thread_event(timestamp=timestamp)
            else:
                self._reducer.thread_event(
                    opcode=opcode,
                    pid=int(values["ProcessId"]),
                    thread_id=int(values["TThreadId"]),
                    timestamp=timestamp,
                )
            return
        if provider == _FILE_PROVIDER:
            if not self._reducer._kernel_setup_or_window(timestamp):
                return
            template = _kernel_template(provider, opcode, version)
            if template is None:
                self._reducer.mark_schema_unknown("file_access")
                return
            # Before qpc_start, only global FileName/FileKey lifecycle changes
            # are correlation inputs.  The suspended child cannot issue FileIo.
            if (
                opcode not in _FILE_NAME_OPCODES
                and (
                    self._reducer._qpc_start is None
                    or timestamp < self._reducer._qpc_start
                )
            ):
                return
            if not self._reducer.inspect_payload(int(record.contents.UserDataLength)):
                return
            values = self._kernel_values(
                record,
                template,
                allow_empty_strings=frozenset({"FileName"}),
            )
            if values is None:
                self._reducer.mark_schema_unknown("file_access")
                return
            if opcode in _FILE_NAME_OPCODES:
                self._reducer.file_name(
                    timestamp=timestamp,
                    file_object=int(values["FileObject"]),
                    raw_identity=str(values["FileName"]),
                    remove=opcode in {35, 36},
                )
                return
            if opcode == 76:
                self._reducer.file_complete(
                    timestamp=timestamp,
                    irp=int(values["IrpPtr"]),
                    status=int(values["NtStatus"]) & 0xFFFFFFFF,
                    exact_pid_scope=pid == self._reducer._pid,
                )
                return
            thread_id = int(values["TTID"])
            if not 0 <= thread_id <= 0xFFFFFFFF:
                # FileIo v2 stores the uint32 TTID in an AMD64 Pointer slot.
                # Nonzero upper bits are schema drift, never a thread ID.
                self._reducer.mark_schema_unknown("file_access")
                return
            self._reducer.file_begin(
                timestamp=timestamp,
                irp=int(values["IrpPtr"]),
                raw_identity=(
                    str(values["OpenPath"]) if opcode == 64 else None
                ),
                thread_id=thread_id,
                header_pid=pid,
                file_object=int(values["FileObject"]),
                file_key=0 if opcode == 64 else int(values["FileKey"]),
                operation=template.operation,
            )
            return
        if provider == _IMAGE_PROVIDER:
            if opcode in {3, 4}:
                return
            if opcode == 10:
                template = _kernel_template(provider, opcode, version)
                if template is None:
                    if target_pid is not None and pid == target_pid:
                        self._reducer.mark_schema_unknown("dll_image_load")
                    return
                payload_pid = self._integer(record, "ProcessId", 4)
                if payload_pid is None:
                    if target_pid is not None and pid == target_pid:
                        self._reducer.mark_schema_unknown("dll_image_load")
                    return
                if target_pid is None or (
                    payload_pid != target_pid and pid != target_pid
                ):
                    return
                if payload_pid != target_pid or pid != target_pid:
                    self._reducer.mark_schema_unknown("dll_image_load")
                    return
                if not self._reducer.inspect_payload(int(record.contents.UserDataLength)):
                    return
                path = self._string(record, "FileName")
                if path is None:
                    self._reducer.mark_schema_unknown("dll_image_load")
                else:
                    self._reducer.image_load(
                        pid=pid, timestamp=timestamp, raw_identity=path
                    )
            return
        if provider == _REGISTRY_PROVIDER:
            if not self._reducer._kernel_setup_or_window(timestamp):
                return
            template = _kernel_template(provider, opcode, version)
            if template is None:
                self._reducer.mark_schema_unknown("registry_access")
                return
            if (
                opcode not in _REGISTRY_LIFECYCLE_OPCODES
                and (
                    self._reducer._qpc_start is None
                    or timestamp < self._reducer._qpc_start
                )
            ):
                return
            if (
                opcode not in _REGISTRY_LIFECYCLE_OPCODES
                and (target_pid is None or pid != target_pid)
            ):
                return
            if not self._reducer.inspect_payload(int(record.contents.UserDataLength)):
                return
            values = self._kernel_values(
                record,
                template,
                allow_empty_strings=frozenset({"KeyName"}),
            )
            if values is None:
                self._reducer.mark_schema_unknown("registry_access")
            elif opcode in _REGISTRY_LIFECYCLE_OPCODES:
                self._reducer.registry_lifecycle(
                    opcode=opcode,
                    timestamp=timestamp,
                    key_handle=int(values["KeyHandle"]),
                    raw_identity=str(values["KeyName"]),
                    status=int(values["Status"]) & 0xFFFFFFFF,
                    initial_time=int(values["InitialTime"]),
                )
            else:
                self._reducer.registry_operation(
                    pid=pid,
                    timestamp=timestamp,
                    raw_identity=str(values["KeyName"]),
                    status=int(values["Status"]) & 0xFFFFFFFF,
                    operation=template.operation,
                    opcode=opcode,
                    key_handle=int(values["KeyHandle"]),
                    initial_time=int(values["InitialTime"]),
                )
            return
        if provider in {_USER_LOADER_PROVIDER, _CI_PROVIDER}:
            plane = (
                "dll_image_load"
                if provider == _USER_LOADER_PROVIDER
                else "code_integrity_policy"
            )
            if provider == _USER_LOADER_PROVIDER:
                if (
                    target_pid is None
                    or pid != target_pid
                    or not self._reducer._timestamp_in_window(timestamp)
                ):
                    return
            elif not self._reducer._timestamp_in_window(timestamp):
                return

            template = _manifest_template(provider, event_id, version)
            if template is None:
                # Full descriptor identity proves shape, not semantics.  An
                # enabled in-window event outside the explicit semantic
                # partition therefore invalidates negative closure.
                if self._reducer.inspect_payload(
                    int(record.contents.UserDataLength)
                ):
                    self._reducer.mark_unknown_manifest_event(
                        plane=plane, pid=pid, timestamp=timestamp
                    )
                return
            if not self._reducer.inspect_payload(int(record.contents.UserDataLength)):
                return
            values = self._manifest_values(record, template)
            if values is None:
                self._reducer.mark_schema_unknown(plane)
                return

            process_identity = (
                values.get(template.process_field)
                if template.process_field is not None
                else None
            )
            object_identity = (
                values.get(template.object_field)
                if template.object_field is not None
                else None
            )
            status = (
                values.get(template.status_field)
                if template.status_field is not None
                else None
            )
            if (
                process_identity is not None
                and type(process_identity) is not str
            ) or (
                object_identity is not None and type(object_identity) is not str
            ) or (status is not None and type(status) is not int):
                self._reducer.mark_schema_unknown(plane)
                return

            if provider == _USER_LOADER_PROVIDER:
                if event_id == 5 and values.get("ProcessId") != target_pid:
                    self._reducer.mark_schema_unknown(plane)
                    return
                self._reducer.user_loader_observation(
                    pid=pid,
                    timestamp=timestamp,
                    semantic=template.semantic,
                    raw_process_identity=process_identity,
                    raw_object_identity=object_identity,
                    failure_status=status,
                )
            else:
                self._reducer.code_integrity_observation(
                    timestamp=timestamp,
                    semantic=template.semantic,
                    raw_process_identity=process_identity,
                    raw_object_identity=object_identity,
                )


__all__ = (
    "CURRENT_RUNTIME_CANDIDATE",
    "EXACT_KERNEL_ENABLE_FLAGS",
    "EXACT_LOG_FILE_MODE",
    "InventoryBinding",
    "MAX_CALLBACKS",
    "MAX_CHILD_RECORDS",
    "MAX_DURATION_SECONDS",
    "MAX_INSPECTED_PAYLOAD_BYTES",
    "MAX_PENDING_IRPS",
    "PLANE_ORDER",
    "PlaneTraceResult",
    "RealtimeRuntimeCollector",
    "RuntimeTraceError",
    "RuntimeTraceResult",
    "SESSION_GUID",
    "SESSION_NAME",
    "SUBJECT_ACCESS_DENIED",
)
