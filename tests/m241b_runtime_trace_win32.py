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
EVENT_TRACE_FLAG_IMAGE_LOAD = 0x00000004
EVENT_TRACE_FLAG_REGISTRY = 0x00020000
EVENT_TRACE_FLAG_FILE_IO = 0x02000000
EVENT_TRACE_FLAG_FILE_IO_INIT = 0x04000000
EVENT_TRACE_FLAG_NO_SYSCONFIG = 0x10000000
EXACT_KERNEL_ENABLE_FLAGS = (
    EVENT_TRACE_FLAG_PROCESS
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
MAX_PROPERTY_BYTES = 64 * 1024

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
    "file_access": frozenset({"file_create"}),
    "dll_image_load": frozenset({"image_map"}),
    "registry_access": frozenset(
        {"registry_open", "registry_query", "registry_query_value"}
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


class InventoryBinding:
    """Typed allowlist plus a deliberately non-revealing raw identity mapper."""

    __slots__ = ("runtime_digest", "_objects", "_resolver")

    def __init__(
        self,
        *,
        runtime_digest: str,
        objects_by_plane: Mapping[str, tuple[str, ...]],
        resolver: InventoryResolver,
    ) -> None:
        if (
            type(runtime_digest) is not str
            or _DIGEST.fullmatch(runtime_digest) is None
            or type(objects_by_plane) is not dict
            or tuple(objects_by_plane) != PLANE_ORDER
            or not callable(resolver)
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
        self.runtime_digest = runtime_digest
        self._objects = MappingProxyType(copied)
        self._resolver = resolver

    def contains(self, plane: str, object_ref: str) -> bool:
        return plane in self._objects and object_ref in self._objects[plane]

    def resolve(self, plane: str, raw_identity: str) -> str | None:
        if plane not in self._objects or type(raw_identity) is not str:
            return None
        try:
            value = self._resolver(plane, raw_identity)
        except BaseException:
            return None
        if type(value) is not str or value not in self._objects[plane]:
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


class _TraceReducer:
    """Raw-free state machine shared by native callbacks and pure fault tests."""

    def __init__(self, inventory: InventoryBinding) -> None:
        self._inventory = inventory
        self._planes = {plane: _PlaneState() for plane in PLANE_ORDER}
        self._pending: dict[int, _PendingFile] = {}
        self._pid: int | None = None
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
        self._scope_complete = {plane: True for plane in PLANE_ORDER}
        self._correlation_complete = {plane: True for plane in PLANE_ORDER}
        self._lossless = True
        self._overflowed = False
        self._lock = threading.RLock()

    def bind(self, *, pid: int, qpc_start: int, initial_image_ref: str) -> None:
        with self._lock:
            if (
                self._pid is not None
                or type(pid) is not int
                or pid <= 0
                or type(qpc_start) is not int
                or qpc_start <= 0
                or type(initial_image_ref) is not str
                or not self._inventory.contains("dll_image_load", initial_image_ref)
            ):
                _fail("trace_binding_invalid")
            self._pid = pid
            self._qpc_start = qpc_start
            # A successfully created suspended child proves the initial image was
            # mapped.  This is not an ETW rundown event and proves no CI absence.
            if self._record("dll_image_load"):
                self._success("dll_image_load", "image_map")

    def end_window(self, qpc_end: int) -> None:
        with self._lock:
            if (
                self._qpc_start is None
                or self._qpc_end is not None
                or type(qpc_end) is not int
                or qpc_end < self._qpc_start
            ):
                self._global_reasons.add("observation_ambiguous")
                return
            self._qpc_end = qpc_end

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
        elif reason == "observation_overflow":
            self._overflowed = True
        elif reason == "observation_ambiguous":
            self._correlation_complete[plane] = False

    def _correlation_failure(self, plane: str) -> None:
        self._correlation_complete[plane] = False
        self._reason(plane, "observation_ambiguous")

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

    def mark_schema_unknown(self, plane: str) -> None:
        with self._lock:
            if plane in self._planes:
                self._schema_proved[plane] = False
                self._reason(plane, "observation_ambiguous")
            else:
                for item in PLANE_ORDER:
                    self._schema_proved[item] = False
                self._global_reasons.add("observation_ambiguous")

    def mark_manifest_unavailable(self, plane: str) -> None:
        with self._lock:
            if plane in self._planes:
                self._probe_available[plane] = False
                self._schema_proved[plane] = False
                self._reason(plane, "probe_unavailable")

    def mark_probe_available(self, plane: str) -> None:
        with self._lock:
            if plane not in self._planes:
                self._global_reasons.add("observation_ambiguous")
                return
            self._probe_available[plane] = True
            self._schema_proved[plane] = True

    # Kept private and exact for the closed manifest adapter seam.
    mark_manifest_available = mark_probe_available

    def expects_file_completion(self) -> bool:
        with self._lock:
            return bool(self._pending)

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
        self, *, pid: int, timestamp: int, irp: int, raw_identity: str
    ) -> None:
        with self._lock:
            if not self._in_window(pid, timestamp):
                return
            if type(irp) is not int or irp <= 0 or not self._record("file_access"):
                self._correlation_failure("file_access")
                return
            if irp in self._pending or len(self._pending) >= MAX_PENDING_IRPS:
                if len(self._pending) >= MAX_PENDING_IRPS:
                    self._reason("file_access", "observation_overflow")
                else:
                    self._correlation_failure("file_access")
                return
            object_ref = self._inventory.resolve("file_access", raw_identity)
            self._pending[irp] = _PendingFile(object_ref)

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
            if status == _STATUS_SUCCESS:
                if pending.object_ref is None:
                    self._reason("file_access", "plane_scope_unproved")
                else:
                    self._success("file_access", "file_create")
            elif status == _STATUS_ACCESS_DENIED:
                self._denial("file_access", pending.object_ref, "file_create")
            else:
                self._reason("file_access", "observation_ambiguous")

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
    ) -> None:
        with self._lock:
            if not self._in_window(pid, timestamp):
                return
            if not self._record("registry_access"):
                return
            object_ref = self._inventory.resolve("registry_access", raw_identity)
            if status == _STATUS_SUCCESS:
                if object_ref is None:
                    self._reason("registry_access", "plane_scope_unproved")
                else:
                    self._success("registry_access", operation)
            elif status == _STATUS_ACCESS_DENIED:
                self._denial("registry_access", object_ref, operation)
            else:
                self._reason("registry_access", "observation_ambiguous")

    def manifest_observation(
        self,
        *,
        plane: str,
        pid: int,
        timestamp: int,
        raw_identity: str,
        denied: bool,
        operation: str,
    ) -> None:
        """Accept only an exact-template manifest adapter's closed semantic."""

        with self._lock:
            if plane not in {"dll_image_load", "code_integrity_policy"}:
                self._global_reasons.add("observation_ambiguous")
                return
            self._probe_available[plane] = True
            self._schema_proved[plane] = True
            if not self._in_window(pid, timestamp):
                return
            if not self._record(plane):
                return
            object_ref = self._inventory.resolve(plane, raw_identity)
            if denied:
                self._denial(plane, object_ref, operation)
            elif object_ref is None:
                self._reason(plane, "plane_scope_unproved")
            else:
                self._success(plane, operation)

    def finish(self) -> RuntimeTraceResult:
        with self._lock:
            if not self._cleanup_proved:
                self._global_reasons.add("cleanup_unproved")
            if self._qpc_end is None or self._pending:
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
                if not state.denials and not state.successes:
                    self._correlation_complete[plane] = False
                item_quality = PlaneTraceQuality(
                    plane,
                    COLLECTION_SCHEMA
                    if self._schema_proved[plane]
                    else UNKNOWN_COLLECTION_SCHEMA,
                    self._probe_available[plane],
                    self._lossless,
                    self._overflowed,
                    self._scope_complete[plane],
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
                elif state.successes:
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
                str(self._qpc_start or 0),
                str(self._qpc_end or 0),
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
    def qpc(self) -> int: ...
    def session_present(self) -> bool: ...
    def start_session(self) -> int: ...
    def enable_session(self, owned_session_handle: int) -> frozenset[str]: ...
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
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._timer: threading.Timer | None = None
        self._session_stopped = False
        self._consumer_closed = False
        self._terminal = False
        self._consumer_status: int | None = None
        self._lock = threading.RLock()

    def start_for_suspended_child(
        self,
        *,
        process_id: int,
        process_handle: int,
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
            ):
                _fail("trace_binding_invalid")
            try:
                resolved_pid = self._port.process_id(process_handle)
                collision = self._port.session_present()
            except RuntimeTraceError:
                raise
            except BaseException:
                _fail("trace_unavailable")
            if resolved_pid != process_id:
                _fail("trace_binding_invalid")
            if collision:
                # Never adopt or stop a pre-existing exact-name session.
                _fail("trace_session_collision")
            self._process_handle = process_handle
            self._pid = process_id
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
                qpc_start = self._port.qpc()
                self._reducer.bind(
                    pid=process_id,
                    qpc_start=qpc_start,
                    initial_image_ref=initial_image_object_ref,
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
            self._consumer_status = self._port.process(self._consumer)
        except BaseException:
            self._consumer_status = -1

    def _stop_owned_once(self) -> bool:
        with self._lock:
            if self._owned_session is None:
                return True
            if self._session_stopped:
                return True
            try:
                result = self._port.stop_session(self._owned_session)
            except BaseException:
                result = StopTraceResult(False, 0, 0, 0)
            if result.loss_observed:
                self._reducer.mark_lost_events()
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
            ):
                _fail("trace_invalid_state")
            self._terminal = True
            if self._timer is not None:
                self._timer.cancel()
            try:
                qpc_end = self._port.qpc()
            except BaseException:
                qpc_end = -1
            self._reducer.end_window(qpc_end)
            try:
                exact_handle = self._port.process_id(self._process_handle) == self._pid
            except BaseException:
                exact_handle = False
            cleanup_ok = exact_handle
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
            if self._thread.is_alive() or self._consumer_status not in {0, 1223}:
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
_IMAGE_PROVIDER_UUID = uuid.UUID("2cb15d1d-5fc1-11d2-abe1-00a0c911f518")
_REGISTRY_PROVIDER_UUID = uuid.UUID("ae53722e-c863-11d2-8659-00c04fa321a1")
_FILE_PROVIDER = _GUID.from_uuid(_FILE_PROVIDER_UUID).key()
_IMAGE_PROVIDER = _GUID.from_uuid(_IMAGE_PROVIDER_UUID).key()
_REGISTRY_PROVIDER = _GUID.from_uuid(_REGISTRY_PROVIDER_UUID).key()
_USER_LOADER_PROVIDER_UUID = uuid.UUID("b059b83f-d946-4b13-87ca-4292839dc2f2")
_CI_PROVIDER_UUID = uuid.UUID("4ee76bd8-3cf4-44a0-a0ac-3937643e37a3")
_USER_LOADER_PROVIDER = _GUID.from_uuid(_USER_LOADER_PROVIDER_UUID).key()
_CI_PROVIDER = _GUID.from_uuid(_CI_PROVIDER_UUID).key()
_REGISTRY_OPERATION_BY_OPCODE = {
    10: "registry_open",
    11: "registry_open",
    13: "registry_query",
    16: "registry_query_value",
    17: "registry_query",
    18: "registry_query_value",
    19: "registry_query_value",
    29: "registry_query",
}


@dataclass(frozen=True, slots=True)
class _KernelEventTemplate:
    provider: uuid.UUID
    opcode: int
    version: int
    plane: str
    operation: str
    fields: tuple[tuple[str, str, int | None], ...]


_KERNEL_EVENT_TEMPLATES = (
    _KernelEventTemplate(
        _FILE_PROVIDER_UUID,
        64,
        2,
        "file_access",
        "file_create",
        (("IrpPtr", "uint", 8), ("OpenPath", "utf16", None)),
    ),
    _KernelEventTemplate(
        _FILE_PROVIDER_UUID,
        76,
        2,
        "file_access",
        "file_create",
        (("IrpPtr", "uint", 8), ("NtStatus", "uint", 4)),
    ),
    _KernelEventTemplate(
        _IMAGE_PROVIDER_UUID,
        10,
        2,
        "dll_image_load",
        "image_map",
        (("ProcessId", "uint", 4), ("FileName", "utf16", None)),
    ),
    *(
        _KernelEventTemplate(
            _REGISTRY_PROVIDER_UUID,
            opcode,
            2,
            "registry_access",
            operation,
            (("Status", "uint", 4), ("KeyName", "utf16", None)),
        )
        for opcode, operation in _REGISTRY_OPERATION_BY_OPCODE.items()
    ),
)


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


@dataclass(frozen=True, slots=True)
class _ManifestEventTemplate:
    provider: uuid.UUID
    event_id: int
    version: int
    property_names: tuple[str, ...]
    binding_fields: tuple[str, ...]
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
        0x40 | 0x200 | 0x2000000000000000,
        "dll_image_load",
    ),
    _ManifestProvider(
        _CI_PROVIDER_UUID,
        0x8000000000000000 | 0x4000000000000000,
        "code_integrity_policy",
    ),
)

_MANIFEST_CAPABILITIES = (
    _ManifestCapability(
        _USER_LOADER_PROVIDER_UUID,
        "dll_image_load",
        ((3, 0), (6, 0), (7, 0), (8, 0), (9, 0)),
        True,
    ),
    # CI becomes denial-capable only after the complete enforced event-ID and
    # version set (including ProcessName+FileName binding) is frozen.
    _ManifestCapability(_CI_PROVIDER_UUID, "code_integrity_policy", (), False),
)

# Intentionally empty until an official event version and complete TDH property
# template is frozen.  A recognized event without an exact template is
# inconclusive; no undocumented offset/status heuristic is permitted.
_MANIFEST_TEMPLATES: tuple[_ManifestEventTemplate, ...] = ()


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
        k.QueryPerformanceCounter.argtypes = (ctypes.POINTER(LONGLONG),)
        k.QueryPerformanceCounter.restype = wintypes.BOOL
        k.GetProcessId.argtypes = (ctypes.c_void_p,)
        k.GetProcessId.restype = ULONG

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

    def qpc(self) -> int:
        value = LONGLONG()
        if not self._kernel.QueryPerformanceCounter(ctypes.byref(value)):
            _fail("trace_unavailable")
        return int(value.value)

    def session_present(self) -> bool:
        raw, props = self._properties()
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

    def enable_session(self, owned_session_handle: int) -> frozenset[str]:
        if type(owned_session_handle) is not int or owned_session_handle <= 0:
            _fail("trace_unavailable")
        handle = TRACEHANDLE(owned_session_handle)
        capable: set[str] = set()
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            kernel_keys = {
                (item.provider, item.opcode, item.version)
                for item in _KERNEL_EVENT_TEMPLATES
            }
            if {
                (_FILE_PROVIDER_UUID, 64, 2),
                (_FILE_PROVIDER_UUID, 76, 2),
            }.issubset(kernel_keys):
                capable.add("file_access")
            required_registry = {
                (_REGISTRY_PROVIDER_UUID, opcode, 2)
                for opcode in _REGISTRY_OPERATION_BY_OPCODE
            }
            if required_registry.issubset(kernel_keys):
                capable.add("registry_access")
        for provider in _MANIFEST_PROVIDERS:
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
                and set(capability.required_events).issubset(template_keys)
                and any(
                    item.provider == provider.provider
                    and item.plane == provider.plane
                    and item.semantic == "denial"
                    and item.property_names
                    and item.binding_fields
                    for item in _MANIFEST_TEMPLATES
                )
            ):
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
                    int(pointer.contents.LogfileHeader.BuffersLost)
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
        raw, props = self._properties()
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
        return StopTraceResult(
            stopped,
            int(props.EventsLost),
            int(props.LogBuffersLost),
            int(props.RealTimeBuffersLost),
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

    def _string(
        self, record: ctypes.POINTER(_EVENT_RECORD), name: str
    ) -> str | None:
        raw = self._property(record, name)
        if raw is None or len(raw) % 2:
            return None
        try:
            value = raw.decode("utf-16-le", errors="strict").rstrip("\0")
        except UnicodeError:
            return None
        if not value or "\0" in value:
            return None
        return value

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
        if provider == _FILE_PROVIDER and opcode == 64:
            if target_pid is None or pid != target_pid:
                return
            template = _kernel_template(provider, opcode, version)
            if template is None:
                self._reducer.mark_schema_unknown("file_access")
                return
            if not self._reducer.inspect_payload(int(record.contents.UserDataLength)):
                return
            irp = self._integer(record, "IrpPtr", 8)
            path = self._string(record, "OpenPath")
            if irp is None or path is None:
                self._reducer.mark_schema_unknown("file_access")
            else:
                self._reducer.file_begin(
                    pid=pid, timestamp=timestamp, irp=irp, raw_identity=path
                )
            return
        if provider == _FILE_PROVIDER and opcode == 76:
            if pid not in {0, target_pid} and not self._reducer.expects_file_completion():
                return
            if not self._reducer.expects_file_completion() and pid != target_pid:
                return
            template = _kernel_template(provider, opcode, version)
            if template is None:
                self._reducer.mark_schema_unknown("file_access")
                return
            if not self._reducer.inspect_payload(int(record.contents.UserDataLength)):
                return
            irp = self._integer(record, "IrpPtr", 8)
            status = self._integer(record, "NtStatus", 4)
            if irp is None or status is None:
                if self._reducer.expects_file_completion():
                    self._reducer.mark_schema_unknown("file_access")
            else:
                self._reducer.file_complete(
                    timestamp=timestamp,
                    irp=irp,
                    status=status & 0xFFFFFFFF,
                    exact_pid_scope=pid == self._reducer._pid,
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
        if provider == _REGISTRY_PROVIDER and opcode in _REGISTRY_OPERATION_BY_OPCODE:
            if target_pid is None or pid != target_pid:
                return
            template = _kernel_template(provider, opcode, version)
            if template is None:
                self._reducer.mark_schema_unknown("registry_access")
                return
            if not self._reducer.inspect_payload(int(record.contents.UserDataLength)):
                return
            path = self._string(record, "KeyName")
            status = self._integer(record, "Status", 4)
            if path is None or status is None:
                self._reducer.mark_schema_unknown("registry_access")
            else:
                self._reducer.registry_operation(
                    pid=pid,
                    timestamp=timestamp,
                    raw_identity=path,
                    status=status & 0xFFFFFFFF,
                    operation=template.operation,
                )
            return
        if provider in {_USER_LOADER_PROVIDER, _CI_PROVIDER}:
            plane = (
                "dll_image_load"
                if provider == _USER_LOADER_PROVIDER
                else "code_integrity_policy"
            )
            # Closed capability seam: provider/event identity is recognized,
            # but no event is interpreted until its exact official version and
            # TDH template has been frozen in _MANIFEST_TEMPLATES.
            if target_pid is not None and pid == target_pid and (
                (provider == _USER_LOADER_PROVIDER and event_id in {3, 6, 7, 8, 9})
                or provider == _CI_PROVIDER
            ):
                if not self._reducer.inspect_payload(int(record.contents.UserDataLength)):
                    return
                self._reducer.mark_schema_unknown(plane)


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
