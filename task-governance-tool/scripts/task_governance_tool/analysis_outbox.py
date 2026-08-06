"""SQLite-independent local analysis descriptor and status outbox."""

from __future__ import annotations

import os
import re
import secrets
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, NoReturn

from task_governance_tool import _analysis_win32 as _win32
from task_governance_tool.analysis_contracts import (
    ANALYSIS_DESCRIPTOR_MAX_BYTES,
    ANALYSIS_STATUS_MAX_BYTES,
    ATTEMPT_OUTCOME_INFERENCE_STATES,
    RETRYABLE_INFERENCE_STATES,
    AnalysisContractError,
    build_descriptor,
    canonical_json_document_bytes,
    descriptor_replay_matches,
    parse_canonical_json_document,
    pending_status,
    validate_descriptor,
    validate_status,
    validate_status_transition,
)
from task_governance_tool.analysis_packet import (
    AnalysisPacket,
    AnalysisPacketError,
    FIXED_PROMPT_DIGEST,
    revalidate_analysis_packet,
)
from task_governance_tool.analysis_renderer import REPORT_MARKDOWN_MAX_BYTES
from task_governance_tool.analysis_validator import (
    REPORT_JSON_MAX_BYTES,
    AnalysisValidationError,
    ValidatedAdapterOutput,
    ValidatedAnalysisReport,
    validate_recovery_report_document,
    validate_report_document,
)
from task_governance_tool._analysis_windows_process import (
    AbortedAttemptTreeProof,
    AttemptRootCapability,
    AttemptTreeProof,
    BorrowedPublicationRoot,
    DiscardedAttemptTreeProof,
    MockBinding,
    ProcessQuarantineRequired,
    borrow_publish_ready_attempt_root,
    consume_attempt_tree_for_publication,
    create_physical_attempt_root_capability,
    discard_aborted_attempt_root,
    discard_attempt_tree,
    remove_borrowed_publication_root,
)
from task_governance_tool.evidence_consumer import (
    EvidenceConsumerError,
    ValidatedEvidenceSource,
    revalidate_validated_source,
)
from task_governance_tool.state_paths import AnalysisStatePaths


ANALYSIS_MAX_DURABLE_FILES = 100_000
ANALYSIS_MAX_QUARANTINE_ROOTS = 32

_JOB_FILE = re.compile(r"^tg_analysis_job_[0-9a-f]{16}\.json$")
_REPORT_JSON = re.compile(r"^tg_analysis_report_[0-9a-f]{16}\.json$")
_REPORT_MARKDOWN = re.compile(r"^tg_analysis_report_[0-9a-f]{16}\.md$")
_QUARANTINE_ROOT = re.compile(r"^\.taskgov-analysis-[a-z0-9]{8}$")
_STATUS_TEMP = re.compile(r"^\.taskgov-analysis-status-[a-z0-9]{8}\.tmp$")
_SEALED_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass
class AnalysisOutboxError(RuntimeError):
    code: str
    message: str
    contended: bool = False

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class EnqueueResult:
    descriptor: dict[str, Any]
    status: dict[str, Any]
    replayed: bool


@dataclass(frozen=True)
class BoundAnalysisJob:
    descriptor: dict[str, Any]
    status: dict[str, Any]


_SELECTION_KINDS = frozenset({"pending", "recover_intent", "reclaim_running"})
_SELECTION_RESULT_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class SelectedAnalysisJob:
    """One fresh caller-owned copy of a fully validated selection."""

    kind: str
    descriptor: dict[str, Any]
    status: dict[str, Any]

    def __init__(
        self,
        kind: str,
        descriptor: dict[str, Any],
        status: dict[str, Any],
        *,
        _token: object,
    ) -> None:
        if _token is not _SELECTION_RESULT_TOKEN or kind not in _SELECTION_KINDS:
            raise AnalysisOutboxError(
                "analysis_selection_invalid",
                "analysis selection is invalid",
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "descriptor", deepcopy(descriptor))
        object.__setattr__(self, "status", deepcopy(status))


@dataclass(frozen=True)
class StatusCasResult:
    status: dict[str, Any]
    disposition: str

    @property
    def applied(self) -> bool:
        return self.disposition in {
            "unchanged",
            "replaced",
            "ambiguous_applied",
        }


@dataclass(frozen=True)
class PublicationResult:
    """Bounded outcome from a lease-consuming publication operation."""

    status: dict[str, Any] | None
    disposition: str
    lease_retained: bool = False

    def __post_init__(self) -> None:
        if self.disposition not in {"published", "failed", "deferred"}:
            _failure("analysis_publication_invalid")
        if self.lease_retained and self.disposition != "deferred":
            _failure("analysis_publication_invalid")
        if self.status is None and not (
            self.disposition == "deferred" and self.lease_retained
        ):
            _failure("analysis_publication_invalid")


_CONTROLLER_RESOURCE_CATEGORIES = frozenset(
    {
        "B",
        "T",
        "J",
        "restricted_token",
        "mapping",
        "event",
        "pipe",
        "stdio",
        "worker",
    }
)
_NO_ADAPTER_PROOF_TOKEN = object()
_ADAPTER_TREE_SLOT_TOKEN = object()


class NoAdapterControllerProof:
    """Unforgeable-by-construction proof owned by exactly one live session."""

    __slots__ = ("_consumed", "_session_token")

    def __init__(self, session_token: object, *, _token: object) -> None:
        if _token is not _NO_ADAPTER_PROOF_TOKEN:
            _failure("analysis_no_adapter_proof_invalid")
        self._session_token = session_token
        self._consumed = False


class AdapterTreeSlot:
    """Factory-sealed session ownership for one physical adapter root."""

    __slots__ = (
        "_attempt_number",
        "_binding",
        "_root_basename",
        "_root_capability",
        "_root_handle",
        "_root_identity",
        "_session_token",
        "_state",
        "_temporary_identity",
        "_temporary_parent",
    )

    def __init__(
        self,
        *,
        session_token: object,
        attempt_number: int,
        binding: MockBinding,
        temporary_parent: Any,
        temporary_identity: Any,
        root_handle: Any,
        root_identity: Any,
        root_basename: str,
        root_capability: AttemptRootCapability,
        _token: object,
    ) -> None:
        if (
            _token is not _ADAPTER_TREE_SLOT_TOKEN
            or type(attempt_number) is not int
            or attempt_number not in {1, 2}
            or type(binding) is not MockBinding
            or type(root_capability) is not AttemptRootCapability
            or not root_capability.is_physical
            or root_capability.state != "bound"
        ):
            _failure("analysis_adapter_tree_invalid")
        self._attempt_number = attempt_number
        self._binding = binding
        self._root_basename = root_basename
        self._root_capability = root_capability
        self._root_handle = root_handle
        self._root_identity = root_identity
        self._session_token = session_token
        self._state = "active"
        self._temporary_identity = temporary_identity
        self._temporary_parent = temporary_parent

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    @property
    def root_capability(self) -> AttemptRootCapability:
        if self._state != "active":
            _failure("analysis_adapter_tree_invalid")
        return self._root_capability

    def __repr__(self) -> str:
        return (
            "AdapterTreeSlot("
            f"attempt_number={self._attempt_number!r}, "
            f"binding={self._binding!r}, state={self._state!r})"
        )

    def __copy__(self) -> NoReturn:
        _failure("analysis_adapter_tree_invalid")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_adapter_tree_invalid")


class _AnalysisLeaseReleaseUncertain(BaseException):
    """Fatal state after unlock succeeded but owned-handle release did not."""

    def __init__(self, phase: str, *, resources: tuple[object, ...]) -> None:
        super().__init__("analysis lease release could not be proved safely")
        self.phase = phase
        self.resources = resources


@dataclass
class _HeldPublicationLeaf:
    """One TH whose exact parent/name changes at most once."""

    handle: Any
    original: Any
    temporary_parent: Any
    temporary_basename: str
    destination_parent: Any
    destination_basename: str
    content: bytes
    maximum: int
    promoted: bool = False

    def _location(self) -> tuple[Any, str]:
        if self.promoted:
            return self.destination_parent, self.destination_basename
        return self.temporary_parent, self.temporary_basename

    def prove(self) -> None:
        if self.handle is None:
            _failure("analysis_publication_invalid")
        parent, basename = self._location()
        observed = _win32.prove_held_membership(self.handle, parent, basename)
        if (
            not self.original.same_object(observed)
            or observed.size != len(self.content)
            or _win32.read_handle_capped(self.handle, maximum=self.maximum)
            != self.content
        ):
            _failure("analysis_publication_invalid")

    def promote(self) -> None:
        if self.handle is None or self.promoted:
            _failure("analysis_publication_invalid")
        cleared = _win32.set_delete_disposition(self.handle, delete=False)
        if not self.original.same_object(cleared):
            _failure("analysis_publication_invalid")
        renamed = _win32.rename_handle_no_replace(
            self.handle,
            self.destination_parent,
            self.destination_basename,
        )
        if not self.original.same_object(renamed):
            _failure("analysis_publication_invalid")
        self.promoted = True
        self.prove()

    def rollback(self) -> None:
        if self.handle is None:
            return
        current = _win32.query_handle_identity(self.handle)
        if not self.original.same_object(current):
            raise _win32.Win32QuarantineRequired(
                "publication_leaf_identity_unproved",
                handle=self.handle,
                resources=(
                    self.temporary_parent,
                    self.destination_parent,
                    self.original,
                ),
            )
        if not current.delete_pending:
            current = _win32.set_delete_disposition(self.handle, delete=True)
            if not self.original.same_object(current):
                raise _win32.Win32QuarantineRequired(
                    "publication_leaf_delete_unproved",
                    handle=self.handle,
                    resources=(
                        self.temporary_parent,
                        self.destination_parent,
                        self.original,
                    ),
                )
        parent, basename = self._location()
        _win32.rollback_relative_handle(
            self.handle,
            parent,
            basename,
            original=self.original,
        )
        self.handle = None

    def close_published(self) -> None:
        if self.handle is None or not self.promoted:
            _failure("analysis_publication_invalid")
        self.prove()
        try:
            self.handle.close()
        except _win32.Win32BoundaryError as close_failure:
            raise _win32.Win32QuarantineRequired(
                "published_leaf_close_unproved",
                handle=self.handle,
                resources=(self.destination_parent, self.original),
            ) from close_failure
        self.handle = None


@dataclass
class _NoAdapterPublicationTree:
    """Session-created proof object; callers cannot assert a naked tree flag."""

    root_handle: Any
    root_parent: Any
    root_basename: str
    json_leaf: _HeldPublicationLeaf
    markdown_leaf: _HeldPublicationLeaf

    def prove_exact(self) -> None:
        self.json_leaf.prove()
        self.markdown_leaf.prove()
        entries = _win32.enumerate_held_directory(
            self.root_handle,
            maximum_entries=2,
        )
        expected = {
            self.json_leaf.temporary_basename: self.json_leaf.original,
            self.markdown_leaf.temporary_basename: self.markdown_leaf.original,
        }
        if len(entries) != 2 or {item.name for item in entries} != set(expected):
            _failure("analysis_publication_invalid")
        for entry in entries:
            identity = expected[entry.name]
            if (
                entry.is_directory
                or entry.is_reparse
                or entry.file_id != identity.file_id
                or entry.size != identity.size
            ):
                _failure("analysis_publication_invalid")

    def remove_empty_root(self) -> None:
        if self.root_handle is None:
            _failure("analysis_publication_invalid")
        _win32.prove_held_directory_empty(self.root_handle)
        _win32.remove_relative_directory(
            self.root_handle,
            self.root_parent,
            self.root_basename,
        )
        self.root_handle = None

    def rollback(self) -> None:
        # Markdown first keeps JSON as the last matching durable rollback.
        self.markdown_leaf.rollback()
        self.json_leaf.rollback()
        if self.root_handle is not None:
            self.remove_empty_root()


@dataclass
class _AdapterPublicationTree:
    """Held report leaves rooted in one process-proved physical attempt tree."""

    borrowed_root: BorrowedPublicationRoot
    json_leaf: _HeldPublicationLeaf | None = None
    markdown_leaf: _HeldPublicationLeaf | None = None
    root_removed: bool = False

    def prove_exact(self) -> None:
        if self.root_removed or self.json_leaf is None or self.markdown_leaf is None:
            _failure("analysis_publication_invalid")
        self.json_leaf.prove()
        self.markdown_leaf.prove()
        entries = _win32.enumerate_held_directory(
            self.borrowed_root.root_handle,
            maximum_entries=2,
        )
        expected = {
            self.json_leaf.temporary_basename: self.json_leaf.original,
            self.markdown_leaf.temporary_basename: self.markdown_leaf.original,
        }
        if len(entries) != 2 or {item.name for item in entries} != set(expected):
            _failure("analysis_publication_invalid")
        for entry in entries:
            identity = expected[entry.name]
            if (
                entry.is_directory
                or entry.is_reparse
                or entry.file_id != identity.file_id
                or entry.size != identity.size
            ):
                _failure("analysis_publication_invalid")

    def remove_empty_root(
        self,
        *,
        owner_token: object,
        binding: MockBinding,
        attempt_number: int,
    ) -> None:
        if self.root_removed:
            _failure("analysis_publication_invalid")
        remove_borrowed_publication_root(
            self.borrowed_root,
            root_owner_token=owner_token,
            binding=binding,
            attempt_number=attempt_number,
        )
        self.root_removed = True

    def rollback(
        self,
        *,
        owner_token: object,
        binding: MockBinding,
        attempt_number: int,
    ) -> None:
        if self.markdown_leaf is not None:
            self.markdown_leaf.rollback()
        if self.json_leaf is not None:
            self.json_leaf.rollback()
        if not self.root_removed:
            self.remove_empty_root(
                owner_token=owner_token,
                binding=binding,
                attempt_number=attempt_number,
            )


@dataclass
class _PublicationParents:
    root: Any
    reports: Any
    rendered: Any
    temporary: Any | None

    def close(self) -> None:
        # ``root`` is borrowed from the session lease and is released only by
        # the lease owner after every child DP/R0 handle has closed.
        for name in ("temporary", "rendered", "reports"):
            handle = getattr(self, name)
            if handle is not None:
                try:
                    handle.close()
                except _win32.Win32BoundaryError as close_failure:
                    raise _win32.Win32QuarantineRequired(
                        "publication_parent_close_unproved",
                        handle=handle,
                        resources=(self,),
                    ) from close_failure
                setattr(self, name, None)


def _failure(
    code: str = "analysis_outbox_invalid",
    message: str = "analysis outbox could not be changed safely",
    *,
    contended: bool = False,
) -> NoReturn:
    raise AnalysisOutboxError(code, message, contended)


def _selection_kind_for_status(status: dict[str, Any]) -> str | None:
    state = status["state"]
    if state == "pending":
        return "pending"
    if state != "running":
        return None
    r3 = tuple(
        status[field] for field in ("report_id", "report_digest", "render_digest")
    )
    if all(value is not None for value in r3):
        return "recover_intent"
    if all(value is None for value in r3):
        return "reclaim_running"
    _failure("analysis_selection_invalid", "analysis selection is invalid")


def _selected_analysis_job(
    kind: str,
    descriptor: object,
    status: object,
) -> SelectedAnalysisJob:
    try:
        bound = validate_descriptor(descriptor)
        normalized = validate_status(status, descriptor=bound)
    except AnalysisContractError as failure:
        raise AnalysisOutboxError(
            "analysis_selection_invalid",
            "analysis selection is invalid",
        ) from failure
    if _selection_kind_for_status(normalized) != kind:
        _failure("analysis_selection_invalid", "analysis selection is invalid")
    return SelectedAnalysisJob(
        kind,
        bound,
        normalized,
        _token=_SELECTION_RESULT_TOKEN,
    )


class _AnalysisLeaseOwnership:
    """Explicit lease ownership; no finalizer may release a quarantined lock."""

    __slots__ = (
        "_byte_lock",
        "_lock_handle",
        "_lock_identity",
        "_lock_basename",
        "_locked",
        "_parent_handle",
        "_parent_identity",
        "_released",
        "_retained",
        "_root_handle",
        "_root_identity",
        "_root_basename",
        "_security",
        "_state",
    )

    def __init__(
        self,
        *,
        parent_handle: Any,
        root_handle: Any,
        lock_handle: Any,
        byte_lock: Any,
        security: Any,
        parent_identity: Any,
        root_identity: Any,
        lock_identity: Any,
        root_basename: str,
        lock_basename: str,
    ) -> None:
        self._parent_handle = parent_handle
        self._root_handle = root_handle
        self._lock_handle = lock_handle
        self._byte_lock = byte_lock
        self._security = security
        self._parent_identity = parent_identity
        self._root_identity = root_identity
        self._lock_identity = lock_identity
        self._root_basename = root_basename
        self._lock_basename = lock_basename
        self._locked = True
        self._released = False
        self._retained = False
        self._state = "locked"

    @staticmethod
    def _prove_directory(
        handle: Any,
        parent: Any,
        basename: str,
        original: Any,
    ) -> None:
        observed = _win32.query_handle_identity(handle)
        parent_identity = _win32.query_handle_identity(parent)
        expected_name = _win32.query_handle_name(parent).rstrip("\\") + "\\" + basename
        if (
            not original.same_object(observed)
            or not observed.is_directory
            or observed.is_reparse
            or observed.delete_pending
            or observed.link_count != 1
            or observed.volume_serial_number != parent_identity.volume_serial_number
            or _win32.query_handle_name(handle) != expected_name
        ):
            raise _win32.Win32BoundaryError(
                "analysis_membership_unproved",
                "analysis process boundary could not be proved safely",
            )

    def _prove_locked_chain(self) -> None:
        if self._state != "locked" or not self._locked:
            _failure("analysis_session_invalid", "analysis session state is invalid")
        parent = _win32.query_handle_identity(self._parent_handle)
        if (
            not self._parent_identity.same_object(parent)
            or not parent.is_directory
            or parent.is_reparse
            or parent.delete_pending
            or parent.link_count != 1
        ):
            raise _win32.Win32BoundaryError()
        self._prove_directory(
            self._root_handle,
            self._parent_handle,
            self._root_basename,
            self._root_identity,
        )
        lock = _win32.prove_held_membership(
            self._lock_handle,
            self._root_handle,
            self._lock_basename,
        )
        if (
            not self._lock_identity.same_object(lock)
            or self._byte_lock.handle is not self._lock_handle
            or self._byte_lock.released
        ):
            raise _win32.Win32BoundaryError()

    def borrow_root(self) -> Any:
        self._prove_locked_chain()
        return self._root_handle

    def retain_for_quarantine(self) -> None:
        if self._released or self._retained or not self._locked:
            _failure("analysis_session_invalid", "analysis session state is invalid")
        self._retained = True
        self._state = "retained"

    def release_normal(self) -> None:
        if self._released or self._retained or not self._locked:
            _failure("analysis_session_invalid", "analysis session state is invalid")
        try:
            self._prove_locked_chain()
            _win32.unlock_byte_zero(self._byte_lock)
        except _win32.Win32BoundaryError as unlock_failure:
            self._retained = True
            self._state = "retained"
            raise _win32.Win32QuarantineRequired(
                "analysis_lease_unlock_unproved",
                handle=self._lock_handle,
                lock=self._byte_lock,
                resources=(
                    self._root_handle,
                    self._parent_handle,
                    self._security,
                ),
            ) from unlock_failure
        self._locked = False
        self._state = "unlocked"
        for phase, resource in (
            ("lock_close_unproved", self._lock_handle),
            ("root_close_unproved", self._root_handle),
            ("parent_close_unproved", self._parent_handle),
            ("security_close_unproved", self._security),
        ):
            try:
                resource.close()
            except _win32.Win32BoundaryError as close_failure:
                self._state = "release_uncertain"
                raise _AnalysisLeaseReleaseUncertain(
                    phase,
                    resources=(
                        self._lock_handle,
                        self._root_handle,
                        self._parent_handle,
                        self._security,
                        self._byte_lock,
                    ),
                ) from close_failure
        self._released = True
        self._state = "released"


def _close_acquisition_resources(
    *resources: object,
    failure: BaseException,
) -> None:
    for resource in resources:
        if resource is None or bool(getattr(resource, "closed", False)):
            continue
        try:
            resource.close()
        except _win32.Win32BoundaryError as close_failure:
            raise _win32.Win32QuarantineRequired(
                "analysis_lease_acquire_cleanup_unproved",
                handle=resource if isinstance(resource, _win32.OwnedHandle) else None,
                resources=resources + (failure,),
            ) from close_failure


def _acquire_analysis_lease(paths: AnalysisStatePaths) -> _AnalysisLeaseOwnership:
    """Acquire one held-parent-relative exact lease and retain its full chain."""

    if os.name != "nt":
        _failure(
            "analysis_unsupported",
            "analysis outbox is unsupported on this platform",
        )
    parent = root = lock_handle = root_security = lease_security = None
    try:
        parent = _win32.open_no_follow(
            paths.root.parent,
            _win32.R0,
            expect_directory=True,
            kind="analysis-state-parent",
            lease_parent_busy_on_sharing_violation=True,
        )
        parent_identity = _win32.query_handle_identity(parent)
        root_security = _win32.ExplicitSecurityDescriptor.root()
        try:
            root = _win32.create_relative_directory(
                parent,
                paths.root.name,
                root_security,
                kind="analysis-root",
            )
        except _win32.Win32BoundaryError as create_failure:
            if create_failure.code != "analysis_destination_exists":
                raise
            root = _win32.open_relative_directory(
                parent,
                paths.root.name,
                _win32.R0,
                kind="analysis-root",
            )
        _win32.prove_exact_handle_security(root, root_security)
        try:
            root_security.close()
        except _win32.Win32BoundaryError as close_failure:
            raise _win32.Win32QuarantineRequired(
                "analysis_root_security_cleanup_unproved",
                handle=root,
                resources=(parent, root, root_security),
            ) from close_failure
        root_security = None
        root_identity = _win32.query_handle_identity(root)
        _AnalysisLeaseOwnership._prove_directory(
            root,
            parent,
            paths.root.name,
            root_identity,
        )

        lease_security = _win32.ExplicitSecurityDescriptor.lease()
        opened = _win32.open_or_create_relative_lock(
            root,
            paths.lock.name,
            lease_security,
            kind="analysis-lease",
        )
        lock_handle = opened.handle
        _win32.prove_exact_handle_security(lock_handle, lease_security)
        lock_identity = _win32.prove_held_membership(
            lock_handle,
            root,
            paths.lock.name,
        )
        byte_lock = _win32.lock_byte_zero(lock_handle)
        ownership = _AnalysisLeaseOwnership(
            parent_handle=parent,
            root_handle=root,
            lock_handle=lock_handle,
            byte_lock=byte_lock,
            security=lease_security,
            parent_identity=parent_identity,
            root_identity=root_identity,
            lock_identity=lock_identity,
            root_basename=paths.root.name,
            lock_basename=paths.lock.name,
        )
        try:
            ownership._prove_locked_chain()
        except _win32.Win32BoundaryError:
            ownership.release_normal()
            raise
        except BaseException as proof_failure:
            ownership.retain_for_quarantine()
            raise _win32.Win32QuarantineRequired(
                "analysis_lease_post_lock_proof_unproved",
                handle=lock_handle,
                lock=byte_lock,
                resources=(proof_failure, ownership),
            ) from proof_failure
        return ownership
    except _AnalysisLeaseReleaseUncertain:
        raise
    except _win32.Win32QuarantineRequired as failure:
        raise _win32.Win32QuarantineRequired(
            "analysis_lease_acquire_unproved",
            handle=failure.handle,
            lock=failure.lock,
            resources=(failure, parent, root, lock_handle, root_security, lease_security),
        ) from failure
    except BaseException as failure:
        _close_acquisition_resources(
            lock_handle,
            root,
            parent,
            lease_security,
            root_security,
            failure=failure,
        )
        if isinstance(failure, _win32.Win32BoundaryError):
            _failure(
                "analysis_busy"
                if failure.code == "analysis_busy"
                else "analysis_outbox_invalid",
                "analysis outbox is busy"
                if failure.code == "analysis_busy"
                else "analysis outbox could not be changed safely",
                contended=failure.code == "analysis_busy",
            )
        raise


@dataclass
class _HeldSessionDirectories:
    """Session-lifetime R0/S0 ownership for mutable durable namespaces."""

    outbox: Any
    outbox_identity: Any
    outbox_basename: str
    status: Any
    status_identity: Any
    status_basename: str
    status_security: Any
    passive_file_count: int

    def prove(self, root: Any) -> None:
        if (
            type(self.passive_file_count) is not int
            or self.passive_file_count < 0
            or self.passive_file_count > ANALYSIS_MAX_DURABLE_FILES
        ):
            raise _win32.Win32BoundaryError()
        _AnalysisLeaseOwnership._prove_directory(
            self.outbox,
            root,
            self.outbox_basename,
            self.outbox_identity,
        )
        _AnalysisLeaseOwnership._prove_directory(
            self.status,
            root,
            self.status_basename,
            self.status_identity,
        )
        proof = _win32.prove_exact_handle_security(
            self.status,
            self.status_security,
        )
        if proof.policy != "root":
            raise _win32.Win32BoundaryError()

    def close(self) -> None:
        for name in ("status", "outbox", "status_security"):
            handle = getattr(self, name)
            if handle is None:
                continue
            try:
                handle.close()
            except _win32.Win32BoundaryError as close_failure:
                raise _win32.Win32QuarantineRequired(
                    "analysis_session_directory_close_unproved",
                    handle=(
                        handle if isinstance(handle, _win32.OwnedHandle) else None
                    ),
                    resources=(self,),
                ) from close_failure
            setattr(self, name, None)


def _open_or_create_analysis_directory(
    root: Any,
    basename: str,
    *,
    kind: str,
) -> Any:
    security = handle = None
    failure: BaseException | None = None
    try:
        security = _win32.ExplicitSecurityDescriptor.root()
        try:
            handle = _win32.create_relative_directory(
                root,
                basename,
                security,
                kind=kind,
            )
        except _win32.Win32BoundaryError as create_failure:
            if create_failure.code != "analysis_destination_exists":
                raise
            handle = _win32.open_relative_directory(
                root,
                basename,
                _win32.R0,
                kind=kind,
            )
        _win32.prove_exact_handle_security(handle, security)
        return handle
    except _win32.Win32QuarantineRequired as exc:
        failure = exc
        raise
    except BaseException as exc:
        failure = exc
        if handle is not None and not handle.closed:
            raise _win32.Win32QuarantineRequired(
                "analysis_directory_acquisition_unproved",
                handle=handle,
                resources=(root, security, exc),
            ) from exc
        raise
    finally:
        if security is not None and not security.closed:
            try:
                security.close()
            except _win32.Win32BoundaryError as close_failure:
                raise _win32.Win32QuarantineRequired(
                    "analysis_directory_security_cleanup_unproved",
                    handle=handle,
                    resources=(root, security, failure),
                ) from close_failure


def _open_or_create_status_directory(
    root: Any,
    basename: str,
    *,
    kind: str,
) -> tuple[Any, Any]:
    """Directly open/create and retain one exact S0 plus its exact-SD proof."""

    security = handle = None
    try:
        security = _win32.ExplicitSecurityDescriptor.root()
        opened = _win32.open_or_create_status_directory(
            root,
            basename,
            security,
            kind=kind,
        )
        handle = opened.handle
        proof = _win32.prove_exact_handle_security(handle, security)
        if proof.policy != "root":
            raise _win32.Win32BoundaryError()
        return handle, security
    except _win32.Win32QuarantineRequired:
        # The boundary owns a newly-created uncertain S0 through its exception.
        # Closing or reopening either resource would guess at post-create state.
        raise
    except BaseException as failure:
        if handle is not None and not handle.closed:
            raise _win32.Win32QuarantineRequired(
                "analysis_status_directory_acquisition_unproved",
                handle=handle,
                resources=(root, security, failure),
            ) from failure
        if security is not None and not security.closed:
            try:
                security.close()
            except _win32.Win32BoundaryError as close_failure:
                raise _win32.Win32QuarantineRequired(
                    "analysis_status_directory_security_cleanup_unproved",
                    handle=None,
                    resources=(root, security, failure),
                ) from close_failure
        raise


def _close_inventory_handles(
    handles: tuple[Any, ...],
    *,
    failure: BaseException,
) -> None:
    for handle in handles:
        if handle is None or handle.closed:
            continue
        try:
            handle.close()
        except _win32.Win32BoundaryError as close_failure:
            raise _win32.Win32QuarantineRequired(
                "analysis_inventory_close_unproved",
                handle=handle,
                resources=handles + (failure,),
            ) from close_failure


def _prepare_locked_tree(
    paths: AnalysisStatePaths,
    root: Any,
) -> _HeldSessionDirectories:
    """Inventory and open the fixed tree only through the held analysis root."""

    outbox = status = status_security = reports = rendered = temporary = None
    try:
        allowed = {
            paths.lock.name,
            paths.outbox.name,
            paths.status.name,
            paths.reports.name,
            paths.rendered.name,
            paths.temporary.name,
        }
        initial = _win32.enumerate_held_directory(root, maximum_entries=len(allowed))
        if any(item.name not in allowed or item.is_reparse for item in initial):
            _failure()
        for item in initial:
            if item.name == paths.lock.name:
                if item.is_directory:
                    _failure()
            elif not item.is_directory:
                _failure()

        outbox = _open_or_create_analysis_directory(
            root,
            paths.outbox.name,
            kind="analysis-outbox-r0",
        )
        status, status_security = _open_or_create_status_directory(
            root,
            paths.status.name,
            kind="analysis-status-s0",
        )
        reports = _open_or_create_analysis_directory(
            root,
            paths.reports.name,
            kind="analysis-reports-r0",
        )
        rendered = _open_or_create_analysis_directory(
            root,
            paths.rendered.name,
            kind="analysis-rendered-r0",
        )
        temporary = _open_or_create_analysis_directory(
            root,
            paths.temporary.name,
            kind="analysis-temporary-r0",
        )

        directory_handles = {
            paths.outbox.name: outbox,
            paths.status.name: status,
            paths.reports.name: reports,
            paths.rendered.name: rendered,
            paths.temporary.name: temporary,
        }
        final = _win32.enumerate_held_directory(root, maximum_entries=len(allowed))
        if {item.name for item in final} != allowed:
            _failure()
        for entry in final:
            if entry.name == paths.lock.name:
                if entry.is_directory or entry.is_reparse:
                    _failure()
                continue
            handle = directory_handles[entry.name]
            identity = _win32.query_handle_identity(handle)
            _AnalysisLeaseOwnership._prove_directory(
                handle,
                root,
                entry.name,
                identity,
            )
            if (
                not entry.is_directory
                or entry.is_reparse
                or entry.file_id != identity.file_id
            ):
                _failure()

        count = 0
        passive_file_count = 0
        for handle, pattern, allow_temp, passive in (
            (outbox, _JOB_FILE, False, False),
            (status, _JOB_FILE, True, False),
            (reports, _REPORT_JSON, False, True),
            (rendered, _REPORT_MARKDOWN, False, True),
        ):
            entries = _win32.enumerate_held_directory(
                handle,
                maximum_entries=ANALYSIS_MAX_DURABLE_FILES,
            )
            for entry in entries:
                if (
                    entry.is_directory
                    or entry.is_reparse
                    or (
                        pattern.fullmatch(entry.name) is None
                        and not (allow_temp and _STATUS_TEMP.fullmatch(entry.name))
                    )
                ):
                    _failure()
                count += 1
                if passive:
                    passive_file_count += 1
                if count > ANALYSIS_MAX_DURABLE_FILES:
                    _failure(
                        "analysis_too_large",
                        "analysis outbox exceeds the supported size",
                    )

        quarantine = _win32.enumerate_held_directory(
            temporary,
            maximum_entries=ANALYSIS_MAX_QUARANTINE_ROOTS,
        )
        for entry in quarantine:
            if (
                _QUARANTINE_ROOT.fullmatch(entry.name) is None
                or not entry.is_directory
                or entry.is_reparse
            ):
                _failure()

        outbox_identity = _win32.query_handle_identity(outbox)
        status_identity = _win32.query_handle_identity(status)
        held = _HeldSessionDirectories(
            outbox=outbox,
            outbox_identity=outbox_identity,
            outbox_basename=paths.outbox.name,
            status=status,
            status_identity=status_identity,
            status_basename=paths.status.name,
            status_security=status_security,
            passive_file_count=passive_file_count,
        )
        held.prove(root)
        for name, handle in (
            ("reports", reports),
            ("rendered", rendered),
            ("temporary", temporary),
        ):
            try:
                handle.close()
            except _win32.Win32BoundaryError as close_failure:
                raise _win32.Win32QuarantineRequired(
                    "analysis_inventory_close_unproved",
                    handle=handle,
                    resources=(
                        outbox,
                        status,
                        reports,
                        rendered,
                        temporary,
                    ),
                ) from close_failure
            if name == "reports":
                reports = None
            elif name == "rendered":
                rendered = None
            else:
                temporary = None
        held.prove(root)
        return held
    except _win32.Win32QuarantineRequired as failure:
        raise _win32.Win32QuarantineRequired(
            failure.phase,
            handle=failure.handle,
            lock=failure.lock,
            resources=(
                failure,
                temporary,
                rendered,
                reports,
                status,
                outbox,
                status_security,
            ),
        ) from failure
    except BaseException as failure:
        _close_inventory_handles(
            (temporary, rendered, reports, status, outbox, status_security),
            failure=failure,
        )
        if isinstance(failure, _win32.Win32BoundaryError):
            raise AnalysisOutboxError(
                "analysis_outbox_invalid",
                "analysis outbox could not be changed safely",
            ) from failure
        raise


def _close_read_handle(handle: Any, *, resources: tuple[object, ...]) -> None:
    if handle is None or handle.closed:
        return
    try:
        handle.close()
    except _win32.Win32BoundaryError as close_failure:
        raise _win32.Win32QuarantineRequired(
            "analysis_read_close_unproved",
            handle=handle,
            resources=resources,
        ) from close_failure


def _read_descriptor(
    parent: Any,
    basename: str,
    *,
    missing_allowed: bool = False,
) -> dict[str, Any] | None:
    handle = None
    try:
        handle = _win32.open_relative_file_if_present(
            parent,
            basename,
            maximum=ANALYSIS_DESCRIPTOR_MAX_BYTES,
            kind="analysis-descriptor-rh",
        )
        if handle is None:
            if missing_allowed:
                return None
            _failure()
        document = _win32.read_handle_capped(
            handle,
            maximum=ANALYSIS_DESCRIPTOR_MAX_BYTES,
        )
        return validate_descriptor(
            parse_canonical_json_document(
                document,
                maximum=ANALYSIS_DESCRIPTOR_MAX_BYTES,
            )
        )
    except (AnalysisContractError, _win32.Win32BoundaryError) as exc:
        raise AnalysisOutboxError(
            "analysis_outbox_invalid",
            "analysis outbox could not be changed safely",
        ) from exc
    finally:
        _close_read_handle(handle, resources=(parent, basename))


def _read_status(
    parent: Any,
    basename: str,
    *,
    descriptor: object,
    missing_allowed: bool = False,
) -> dict[str, Any] | None:
    handle = None
    try:
        handle = _win32.open_relative_file_if_present(
            parent,
            basename,
            maximum=ANALYSIS_STATUS_MAX_BYTES,
            kind="analysis-status-rh",
        )
        if handle is None:
            if missing_allowed:
                return None
            _failure()
        document = _win32.read_handle_capped(
            handle,
            maximum=ANALYSIS_STATUS_MAX_BYTES,
        )
        return validate_status(
            parse_canonical_json_document(
                document,
                maximum=ANALYSIS_STATUS_MAX_BYTES,
            ),
            descriptor=descriptor,
        )
    except (AnalysisContractError, _win32.Win32BoundaryError) as exc:
        raise AnalysisOutboxError(
            "analysis_outbox_invalid",
            "analysis outbox could not be changed safely",
        ) from exc
    finally:
        _close_read_handle(handle, resources=(parent, basename))


def _freeze_selection_entries(
    parent: Any,
    pattern: re.Pattern[str],
    *,
    allow_status_temp: bool,
    prior_count: int,
) -> tuple[tuple[Any, ...], int]:
    """Take one bounded immutable name/identity inventory from a held parent."""

    frozen: list[Any] = []
    names: set[str] = set()
    count = prior_count
    try:
        entries = _win32.enumerate_held_directory(
            parent,
            maximum_entries=ANALYSIS_MAX_DURABLE_FILES,
        )
        for entry in entries:
            if (
                not isinstance(entry, _win32.DirectoryEntry)
                or type(entry.name) is not str
                or type(entry.file_id) is not bytes
                or len(entry.file_id) != 16
                or type(entry.size) is not int
                or entry.size < 0
                or type(entry.is_directory) is not bool
                or type(entry.is_reparse) is not bool
                or entry.is_directory
                or entry.is_reparse
                or (
                    pattern.fullmatch(entry.name) is None
                    and not (
                        allow_status_temp
                        and _STATUS_TEMP.fullmatch(entry.name) is not None
                    )
                )
            ):
                _failure()
            folded = entry.name.casefold()
            if folded in names:
                _failure()
            names.add(folded)
            count += 1
            if count > ANALYSIS_MAX_DURABLE_FILES:
                _failure(
                    "analysis_too_large",
                    "analysis outbox exceeds the supported size",
                )
            frozen.append(entry)
    except _win32.Win32BoundaryError as failure:
        if failure.code == "analysis_directory_entry_limit":
            raise AnalysisOutboxError(
                "analysis_too_large",
                "analysis outbox exceeds the supported size",
            ) from failure
        raise AnalysisOutboxError(
            "analysis_outbox_invalid",
            "analysis outbox could not be changed safely",
        ) from failure
    return tuple(frozen), count


def _read_inventory_document(
    parent: Any,
    entry: Any,
    *,
    maximum: int,
    kind: str,
) -> bytes:
    """Read exactly the immutable inventory identity through one held RH."""

    handle = None
    try:
        handle = _win32.open_relative_file_if_present(
            parent,
            entry.name,
            maximum=maximum,
            kind=kind,
        )
        if handle is None:
            raise _win32.Win32BoundaryError()
        before = _win32.query_handle_identity(handle)
        membership = _win32.prove_held_membership(handle, parent, entry.name)
        if (
            not before.same_object(membership)
            or before.file_id != entry.file_id
            or before.size != entry.size
        ):
            raise _win32.Win32BoundaryError()
        document = _win32.read_handle_capped(handle, maximum=maximum)
        after = _win32.prove_held_membership(handle, parent, entry.name)
        if (
            before != after
            or after.size != len(document)
            or after.size != entry.size
        ):
            raise _win32.Win32BoundaryError()
        return document
    except _win32.Win32BoundaryError as failure:
        raise AnalysisOutboxError(
            "analysis_outbox_invalid",
            "analysis outbox could not be changed safely",
        ) from failure
    finally:
        _close_read_handle(handle, resources=(parent, entry))


def _read_inventory_descriptor(parent: Any, entry: Any) -> dict[str, Any]:
    try:
        return validate_descriptor(
            parse_canonical_json_document(
                _read_inventory_document(
                    parent,
                    entry,
                    maximum=ANALYSIS_DESCRIPTOR_MAX_BYTES,
                    kind="analysis-selection-descriptor-rh",
                ),
                maximum=ANALYSIS_DESCRIPTOR_MAX_BYTES,
            )
        )
    except AnalysisContractError as failure:
        raise AnalysisOutboxError(
            "analysis_outbox_invalid",
            "analysis outbox could not be changed safely",
        ) from failure


def _read_inventory_status(
    parent: Any,
    entry: Any,
    *,
    descriptor: object,
) -> dict[str, Any]:
    try:
        return validate_status(
            parse_canonical_json_document(
                _read_inventory_document(
                    parent,
                    entry,
                    maximum=ANALYSIS_STATUS_MAX_BYTES,
                    kind="analysis-selection-status-rh",
                ),
                maximum=ANALYSIS_STATUS_MAX_BYTES,
            ),
            descriptor=descriptor,
        )
    except AnalysisContractError as failure:
        raise AnalysisOutboxError(
            "analysis_outbox_invalid",
            "analysis outbox could not be changed safely",
        ) from failure


@dataclass
class _RelativeDurableFile:
    basename: str
    identity: Any
    content: bytes
    maximum: int
    handle: Any | None = None


def _create_relative_durable_file(
    parent: Any,
    basename: str,
    content: bytes,
    *,
    maximum: int,
    kind: str,
    retain_handle: bool = False,
) -> _RelativeDurableFile:
    """Create one durable owner-only leaf through a held session directory."""

    security = handle = original = None
    made_durable = False
    failure: BaseException | None = None
    try:
        security = _win32.ExplicitSecurityDescriptor.report_temp()
        handle = _win32.create_relative_file(
            parent,
            basename,
            _win32.TH,
            security,
            kind=kind,
        )
        original = _win32.query_handle_identity(handle)
        written = _win32.write_flush_reread(handle, content, maximum=maximum)
        if not original.same_object(written):
            _failure("analysis_outbox_invalid")
        durable = _win32.set_delete_disposition(handle, delete=False)
        if not original.same_object(durable):
            _failure("analysis_outbox_invalid")
        made_durable = True
        observed = _win32.prove_held_membership(handle, parent, basename)
        if (
            not original.same_object(observed)
            or observed.size != len(content)
            or _win32.read_handle_capped(handle, maximum=maximum) != content
        ):
            _failure("analysis_outbox_invalid")
        result = _RelativeDurableFile(
            basename,
            observed,
            content,
            maximum,
            handle if retain_handle else None,
        )
        if not retain_handle:
            try:
                handle.close()
            except _win32.Win32BoundaryError as close_failure:
                raise _win32.Win32QuarantineRequired(
                    "analysis_durable_leaf_close_unproved",
                    handle=handle,
                    resources=(parent, basename, original),
                ) from close_failure
            handle = None
        return result
    except _win32.Win32QuarantineRequired as exc:
        failure = exc
        raise
    except BaseException as exc:
        failure = exc
        if handle is not None and not handle.closed:
            if made_durable or original is None:
                raise _win32.Win32QuarantineRequired(
                    "analysis_durable_leaf_state_unproved",
                    handle=handle,
                    resources=(parent, basename, original, exc),
                ) from exc
            try:
                current = _win32.query_handle_identity(handle)
                if not original.same_object(current):
                    raise _win32.Win32BoundaryError()
                if not current.delete_pending:
                    current = _win32.set_delete_disposition(handle, delete=True)
                    if not original.same_object(current):
                        raise _win32.Win32BoundaryError()
                _win32.rollback_relative_handle(
                    handle,
                    parent,
                    basename,
                    original=original,
                )
                handle = None
            except _win32.Win32QuarantineRequired:
                raise
            except _win32.Win32BoundaryError as cleanup_failure:
                raise _win32.Win32QuarantineRequired(
                    "analysis_durable_leaf_cleanup_unproved",
                    handle=handle,
                    resources=(parent, basename, original, exc),
                ) from cleanup_failure
        if isinstance(exc, _win32.Win32BoundaryError):
            raise AnalysisOutboxError(
                "analysis_outbox_invalid",
                "analysis outbox could not be changed safely",
            ) from exc
        raise
    finally:
        if security is not None and not security.closed:
            try:
                security.close()
            except _win32.Win32BoundaryError as close_failure:
                raise _win32.Win32QuarantineRequired(
                    "analysis_durable_security_cleanup_unproved",
                    handle=handle,
                    resources=(parent, basename, original, failure),
                ) from close_failure


def _relative_leaf_present(parent: Any, basename: str, *, maximum: int) -> bool:
    handle = None
    try:
        handle = _win32.open_relative_file_if_present(
            parent,
            basename,
            maximum=maximum,
            kind="analysis-presence-rh",
        )
        return handle is not None
    except _win32.Win32BoundaryError as exc:
        raise AnalysisOutboxError(
            "analysis_outbox_invalid",
            "analysis outbox could not be changed safely",
        ) from exc
    finally:
        _close_read_handle(handle, resources=(parent, basename))


def _cleanup_relative_durable_file(
    parent: Any,
    leaf: _RelativeDurableFile,
) -> None:
    handle = leaf.handle
    if handle is None:
        _failure("analysis_outbox_invalid")
    try:
        observed = _win32.prove_held_membership(handle, parent, leaf.basename)
        if (
            not leaf.identity.same_object(observed)
            or observed.size != len(leaf.content)
            or _win32.read_handle_capped(handle, maximum=leaf.maximum) != leaf.content
        ):
            raise _win32.Win32BoundaryError()
        marked = _win32.set_delete_disposition(handle, delete=True)
        if not leaf.identity.same_object(marked):
            raise _win32.Win32BoundaryError()
        _win32.rollback_relative_handle(
            handle,
            parent,
            leaf.basename,
            original=leaf.identity,
        )
        leaf.handle = None
    except _win32.Win32QuarantineRequired:
        raise
    except _win32.Win32BoundaryError as cleanup_failure:
        raise _win32.Win32QuarantineRequired(
            "analysis_durable_cleanup_unproved",
            handle=handle,
            resources=(parent, leaf),
        ) from cleanup_failure


def _close_relative_durable_file(
    parent: Any,
    leaf: _RelativeDurableFile,
) -> None:
    handle = leaf.handle
    if handle is None:
        _failure("analysis_outbox_invalid")
    try:
        observed = _win32.prove_held_membership(handle, parent, leaf.basename)
        if (
            not leaf.identity.same_object(observed)
            or observed.size != len(leaf.content)
            or _win32.read_handle_capped(handle, maximum=leaf.maximum) != leaf.content
        ):
            raise _win32.Win32BoundaryError()
        handle.close()
        leaf.handle = None
    except _win32.Win32BoundaryError as close_failure:
        raise _win32.Win32QuarantineRequired(
            "analysis_durable_close_unproved",
            handle=handle,
            resources=(parent, leaf),
        ) from close_failure


def _status_from_held_durable_file(
    parent: Any,
    leaf: _RelativeDurableFile,
    *,
    descriptor: object,
) -> dict[str, Any]:
    handle = leaf.handle
    if handle is None:
        _failure("analysis_outbox_invalid")
    try:
        observed = _win32.prove_held_membership(handle, parent, leaf.basename)
        document = _win32.read_handle_capped(handle, maximum=leaf.maximum)
        if (
            not leaf.identity.same_object(observed)
            or observed.size != len(leaf.content)
            or document != leaf.content
        ):
            raise _win32.Win32BoundaryError()
        return validate_status(
            parse_canonical_json_document(document, maximum=leaf.maximum),
            descriptor=descriptor,
        )
    except (AnalysisContractError, _win32.Win32BoundaryError) as exc:
        raise _win32.Win32QuarantineRequired(
            "analysis_status_replace_proof_unproved",
            handle=handle,
            resources=(parent, leaf),
        ) from exc


_SESSION_CONSTRUCTOR_TOKEN = object()


class AnalysisOutboxSession:
    """One explicit lease spanning selection through the caller's final action."""

    __slots__ = (
        "_adapter_slot",
        "_adapter_lane_started",
        "_adapter_last_attempt_number",
        "_controller_ledger",
        "_controller_ledger_sealed",
        "_directories",
        "_lease",
        "_no_adapter_proof",
        "_paths",
        "_publication_active",
        "_retained_resources",
        "_selection_consumed",
        "_discarded_adapter_root",
        "_session_token",
        "_state",
    )

    def __init__(
        self,
        paths: AnalysisStatePaths,
        lease: Any,
        directories: _HeldSessionDirectories | None = None,
        *,
        _token: object,
    ) -> None:
        if _token is not _SESSION_CONSTRUCTOR_TOKEN:
            _failure("analysis_session_invalid", "analysis session state is invalid")
        self._paths = paths
        self._lease = lease
        self._directories = directories
        self._adapter_slot: AdapterTreeSlot | None = None
        self._adapter_lane_started = False
        self._adapter_last_attempt_number = 0
        self._publication_active = False
        self._retained_resources: tuple[object, ...] = ()
        self._selection_consumed = False
        self._discarded_adapter_root: tuple[str, Any] | None = None
        self._controller_ledger = {
            category: False for category in _CONTROLLER_RESOURCE_CATEGORIES
        }
        self._controller_ledger_sealed = False
        self._session_token = object()
        self._no_adapter_proof: NoAdapterControllerProof | None = None
        self._state = "active"

    @classmethod
    def acquire(cls, paths: AnalysisStatePaths) -> AnalysisOutboxSession:
        """Acquire once, then complete locked inventory before returning."""

        lease = _acquire_analysis_lease(paths)
        try:
            directories = _prepare_locked_tree(paths, lease.borrow_root())
        except _win32.Win32QuarantineRequired:
            lease.retain_for_quarantine()
            raise
        except Exception:
            lease.release_normal()
            raise
        except BaseException:
            lease.retain_for_quarantine()
            raise
        return cls(
            paths,
            lease,
            directories,
            _token=_SESSION_CONSTRUCTOR_TOKEN,
        )

    @property
    def state(self) -> str:
        return self._state

    def _require_active(self) -> None:
        if self._state != "active":
            _failure("analysis_session_invalid", "analysis session state is invalid")

    def _borrow_analysis_root(self) -> Any:
        self._require_active()
        if self._directories is None:
            _failure("analysis_session_invalid", "analysis session state is invalid")
        root = None
        try:
            root = self._lease.borrow_root()
            self._directories.prove(root)
            return root
        except _win32.Win32QuarantineRequired:
            raise
        except _win32.Win32BoundaryError as proof_failure:
            raise _win32.Win32QuarantineRequired(
                "analysis_session_membership_unproved",
                handle=root if isinstance(root, _win32.OwnedHandle) else None,
                resources=(self._lease, self._directories, proof_failure),
            ) from proof_failure

    def _prove_session_directories(self) -> None:
        self._borrow_analysis_root()

    def mark_controller_resource_created(self, category: str) -> None:
        """Close one no-adapter ledger category before its process factory."""

        self._require_active()
        if (
            type(category) is not str
            or category not in _CONTROLLER_RESOURCE_CATEGORIES
            or self._controller_ledger_sealed
            or self._controller_ledger[category]
        ):
            _failure("analysis_controller_ledger_invalid")
        self._controller_ledger[category] = True

    def seal_no_adapter_controller_proof(self) -> NoAdapterControllerProof:
        """Seal one single-use proof only when every closed category is zero."""

        self._require_active()
        if (
            self._controller_ledger_sealed
            or self._adapter_lane_started
            or set(self._controller_ledger) != _CONTROLLER_RESOURCE_CATEGORIES
            or any(self._controller_ledger.values())
        ):
            _failure("analysis_no_adapter_proof_invalid")
        proof = NoAdapterControllerProof(
            self._session_token,
            _token=_NO_ADAPTER_PROOF_TOKEN,
        )
        self._controller_ledger_sealed = True
        self._no_adapter_proof = proof
        return proof

    def _consume_no_adapter_controller_proof(
        self,
        proof: NoAdapterControllerProof,
    ) -> None:
        self._require_active()
        if (
            not isinstance(proof, NoAdapterControllerProof)
            or proof is not self._no_adapter_proof
            or proof._session_token is not self._session_token
            or proof._consumed
            or not self._controller_ledger_sealed
            or set(self._controller_ledger) != _CONTROLLER_RESOURCE_CATEGORIES
            or any(self._controller_ledger.values())
        ):
            _failure("analysis_no_adapter_proof_invalid")
        proof._consumed = True
        self._no_adapter_proof = None

    def retain_for_quarantine(self) -> None:
        """Retain the live lease without any unlock or close operation."""

        self._require_active()
        self._lease.retain_for_quarantine()
        self._state = "retained"

    def _retain_publication_resources(self, *resources: object) -> None:
        self._retained_resources = tuple(resources)
        self._publication_active = False
        if self._state == "active":
            self.retain_for_quarantine()
        elif self._state != "retained":
            _failure("analysis_session_invalid", "analysis session state is invalid")

    def release_normal(self) -> None:
        """Perform the sole normal release: UnlockFileEx, then CloseHandle."""

        self._require_active()
        if self._publication_active or self._adapter_slot is not None:
            _failure(
                "analysis_session_invalid",
                "analysis publication resources are still active",
            )
        if self._directories is not None:
            try:
                self._prove_session_directories()
                self._directories.close()
                self._directories = None
            except BaseException as directory_failure:
                self._retained_resources = (directory_failure, self._directories)
                self._lease.retain_for_quarantine()
                self._state = "retained"
                if isinstance(directory_failure, _win32.Win32QuarantineRequired):
                    raise
                raise _win32.Win32QuarantineRequired(
                    "analysis_session_directory_release_unproved",
                    handle=None,
                    resources=self._retained_resources,
                ) from directory_failure
        try:
            self._lease.release_normal()
        except _AnalysisLeaseReleaseUncertain:
            self._state = "release_uncertain"
            raise
        except _win32.Win32QuarantineRequired:
            self._state = "retained"
            raise
        self._state = "released"

    def select_next_job(self) -> SelectedAnalysisJob | None:
        """Validate one immutable inventory, then select at most one job."""

        self._require_active()
        if self._selection_consumed or self._publication_active:
            _failure("analysis_selection_invalid", "analysis selection is invalid")
        self._selection_consumed = True
        self._prove_session_directories()

        outbox_entries, count = _freeze_selection_entries(
            self._directories.outbox,
            _JOB_FILE,
            allow_status_temp=False,
            prior_count=self._directories.passive_file_count,
        )
        status_entries, total_count = _freeze_selection_entries(
            self._directories.status,
            _JOB_FILE,
            allow_status_temp=True,
            prior_count=count,
        )

        outbox_by_name = {entry.name: entry for entry in outbox_entries}
        status_by_name = {
            entry.name: entry
            for entry in status_entries
            if _JOB_FILE.fullmatch(entry.name) is not None
        }
        if set(status_by_name) - set(outbox_by_name):
            _failure()

        candidates: list[
            tuple[str, str, dict[str, Any], dict[str, Any], bool]
        ] = []
        for basename in sorted(outbox_by_name):
            descriptor = _read_inventory_descriptor(
                self._directories.outbox,
                outbox_by_name[basename],
            )
            if basename != f"{descriptor['analysis_job_id']}.json":
                _failure()
            status_entry = status_by_name.get(basename)
            missing_status = status_entry is None
            status = (
                pending_status(descriptor)
                if missing_status
                else _read_inventory_status(
                    self._directories.status,
                    status_entry,
                    descriptor=descriptor,
                )
            )
            kind = _selection_kind_for_status(status)
            if kind is not None:
                candidates.append(
                    (
                        descriptor["analysis_job_id"],
                        kind,
                        descriptor,
                        status,
                        missing_status,
                    )
                )

        self._prove_session_directories()
        if not candidates:
            return None
        _job_id, kind, descriptor, status, missing_status = min(
            candidates,
            key=lambda candidate: candidate[0],
        )
        selected = _selected_analysis_job(kind, descriptor, status)
        if missing_status:
            if total_count >= ANALYSIS_MAX_DURABLE_FILES:
                _failure(
                    "analysis_too_large",
                    "analysis outbox exceeds the supported size",
                )
            _create_relative_durable_file(
                self._directories.status,
                f"{descriptor['analysis_job_id']}.json",
                canonical_json_document_bytes(status),
                maximum=ANALYSIS_STATUS_MAX_BYTES,
                kind="analysis-status-th",
            )
            self._prove_session_directories()
        return selected

    def read_bound_job(self, descriptor: object) -> BoundAnalysisJob:
        """Read one exact descriptor/status pair without reacquiring the lease."""

        self._require_active()
        try:
            bound = validate_descriptor(descriptor)
        except AnalysisContractError as exc:
            raise AnalysisOutboxError(
                "analysis_outbox_invalid",
                "analysis outbox could not be changed safely",
            ) from exc
        name = f"{bound['analysis_job_id']}.json"
        self._prove_session_directories()
        try:
            stored_descriptor = _read_descriptor(self._directories.outbox, name)
            if stored_descriptor != bound:
                _failure("analysis_collision", "analysis job collides with existing state")
            stored_status = _read_status(
                self._directories.status,
                name,
                descriptor=bound,
            )
            return BoundAnalysisJob(stored_descriptor, stored_status)
        finally:
            self._prove_session_directories()

    @staticmethod
    def _temporary_root_inventory(temporary: Any) -> dict[str, Any]:
        """Return one bounded held inventory of inert private roots."""

        entries = _win32.enumerate_held_directory(
            temporary,
            maximum_entries=ANALYSIS_MAX_QUARANTINE_ROOTS,
        )
        for entry in entries:
            if (
                _QUARANTINE_ROOT.fullmatch(entry.name) is None
                or not entry.is_directory
                or entry.is_reparse
            ):
                _failure("analysis_publication_invalid")
        return {entry.name: entry for entry in entries}

    def _require_exact_adapter_slot(
        self,
        slot: AdapterTreeSlot,
        proof: AttemptTreeProof | AbortedAttemptTreeProof | None = None,
    ) -> None:
        self._require_active()
        if (
            type(slot) is not AdapterTreeSlot
            or slot is not self._adapter_slot
            or slot._session_token is not self._session_token
            or slot._state != "active"
            or type(slot._root_capability) is not AttemptRootCapability
            or not slot._root_capability.is_physical
            or slot._root_capability._root_handle is not slot._root_handle
            or slot._root_capability._root_parent is not slot._temporary_parent
            or slot._root_capability._root_basename != slot._root_basename
            or slot._root_handle.closed
            or slot._temporary_parent.closed
        ):
            _failure("analysis_adapter_tree_invalid")
        if proof is not None and (
            type(proof) not in {AttemptTreeProof, AbortedAttemptTreeProof}
            or not proof.is_physical
            or proof.attempt_number != slot._attempt_number
            or proof.binding != slot._binding
            or proof._root_capability is not slot._root_capability
        ):
            _failure("analysis_adapter_tree_invalid")

    def _finish_removed_adapter_slot(self, slot: AdapterTreeSlot) -> None:
        """Close only the exact held tmp parent after process-proved removal."""

        self._require_active()
        if (
            type(slot) is not AdapterTreeSlot
            or slot is not self._adapter_slot
            or slot._session_token is not self._session_token
            or slot._state != "active"
            or slot._root_capability._root_handle is not slot._root_handle
            or slot._root_capability._root_parent is not slot._temporary_parent
            or slot._root_capability._root_basename != slot._root_basename
            or slot._root_capability.state != "removed"
            or not slot._root_handle.closed
            or slot._temporary_parent.closed
        ):
            _failure("analysis_adapter_tree_invalid")
        root = self._borrow_analysis_root()
        _AnalysisLeaseOwnership._prove_directory(
            slot._temporary_parent,
            root,
            self._paths.temporary.name,
            slot._temporary_identity,
        )
        inventory = self._temporary_root_inventory(slot._temporary_parent)
        if slot._root_basename in inventory:
            raise _win32.Win32BoundaryError()
        slot._temporary_parent.close()
        slot._temporary_parent = None
        slot._state = "closed"
        self._adapter_slot = None
        self._prove_session_directories()

    def create_adapter_tree_slot(
        self,
        descriptor: object,
        packet: object,
        binding: MockBinding,
        attempt_number: int,
    ) -> AdapterTreeSlot:
        """Create and retain one fresh physical adapter root under the lease."""

        self._require_active()
        if (
            self._publication_active
            or self._adapter_slot is not None
            or self._controller_ledger_sealed
            or any(self._controller_ledger.values())
            or type(binding) is not MockBinding
            or type(attempt_number) is not int
            or attempt_number not in {1, 2}
        ):
            _failure("analysis_adapter_tree_invalid")
        try:
            bound = validate_descriptor(descriptor)
            normalized_packet = revalidate_analysis_packet(packet, bound)
        except (AnalysisContractError, AnalysisPacketError) as exc:
            raise AnalysisOutboxError(
                "analysis_adapter_tree_invalid",
                "analysis adapter tree input is invalid",
            ) from exc
        job = self.read_bound_job(bound)
        current = job.status
        expected_binding = MockBinding(
            analysis_job_id=bound["analysis_job_id"],
            source_key=bound["source_key"],
            recipe_digest=bound["recipe_digest"],
            packet_digest=normalized_packet.packet_digest,
        )
        no_intent = all(
            current[name] is None
            for name in ("report_id", "report_digest", "render_digest")
        )
        if (
            bound["recipe"]["inference_mode"] != "codex_optional"
            or binding != expected_binding
            or current["state"] != "running"
            or current["packet_digest"] != normalized_packet.packet_digest
            or not no_intent
        ):
            _failure("analysis_adapter_tree_invalid")
        if attempt_number == 1:
            if (
                self._adapter_lane_started
                or self._adapter_last_attempt_number != 0
                or self._discarded_adapter_root is not None
                or current["adapter_attempt_count"] != 0
                or current["inference_state"] != "pending"
                or current["accepted_output_digest"] is not None
            ):
                _failure("analysis_adapter_tree_invalid")
        elif (
            not self._adapter_lane_started
            or self._adapter_last_attempt_number != 1
            or self._discarded_adapter_root is None
            or current["adapter_attempt_count"] != 1
            or current["inference_state"] not in RETRYABLE_INFERENCE_STATES
            or current["accepted_output_digest"] is not None
        ):
            _failure("analysis_adapter_tree_invalid")

        root = self._borrow_analysis_root()
        temporary = root_handle = root_identity = capability = None
        root_basename = ".taskgov-analysis-" + secrets.token_hex(4)
        try:
            temporary = _win32.open_relative_directory(
                root,
                self._paths.temporary.name,
                _win32.R0,
                kind="analysis-adapter-temporary-parent",
            )
            temporary_identity = _win32.query_handle_identity(temporary)
            _AnalysisLeaseOwnership._prove_directory(
                temporary,
                root,
                self._paths.temporary.name,
                temporary_identity,
            )
            before = self._temporary_root_inventory(temporary)
            if len(before) >= ANALYSIS_MAX_QUARANTINE_ROOTS:
                _failure(
                    "analysis_too_large",
                    "analysis temporary root inventory is full",
                )
            if (
                attempt_number == 2
                and root_basename == self._discarded_adapter_root[0]
            ):
                _failure("analysis_adapter_tree_invalid")

            self._prove_session_directories()
            capability = create_physical_attempt_root_capability(
                root_parent=temporary,
                root_basename=root_basename,
                analysis_job_id=bound["analysis_job_id"],
                attempt_number=attempt_number,
                packet_digest=normalized_packet.packet_digest,
                owner_token=self._session_token,
            )
            root_handle = capability._root_handle
            root_identity = capability._root_identity
            after = self._temporary_root_inventory(temporary)
            if (
                (
                    attempt_number == 2
                    and root_identity.same_object(self._discarded_adapter_root[1])
                )
                or set(after) != set(before) | {root_basename}
                or after[root_basename].file_id != root_identity.file_id
                or any(
                    after[name] != entry
                    for name, entry in before.items()
                )
            ):
                capability._state = "quarantine"
                raise ProcessQuarantineRequired(
                    (),
                    frozenset({f"n{attempt_number}:root"}),
                    root_capability=capability,
                )
            slot = AdapterTreeSlot(
                session_token=self._session_token,
                attempt_number=attempt_number,
                binding=binding,
                temporary_parent=temporary,
                temporary_identity=temporary_identity,
                root_handle=root_handle,
                root_identity=root_identity,
                root_basename=root_basename,
                root_capability=capability,
                _token=_ADAPTER_TREE_SLOT_TOKEN,
            )
            self._adapter_slot = slot
            self._adapter_lane_started = True
            self._adapter_last_attempt_number = attempt_number
            return slot
        except ProcessQuarantineRequired as failure:
            self._retain_publication_resources(
                failure,
                root_handle,
                root_identity,
                temporary,
                capability,
            )
            raise
        except _win32.Win32QuarantineRequired as failure:
            self._retain_publication_resources(
                failure,
                root_handle,
                root_identity,
                temporary,
                capability,
            )
            if capability is not None:
                capability._state = "quarantine"
                raise ProcessQuarantineRequired(
                    (),
                    frozenset({f"n{attempt_number}:root"}),
                    root_capability=capability,
                ) from failure
            raise
        except BaseException as failure:
            if capability is not None:
                capability._state = "quarantine"
                self._retain_publication_resources(
                    failure,
                    capability,
                    root_handle,
                    root_identity,
                    temporary,
                )
                raise ProcessQuarantineRequired(
                    (),
                    frozenset({f"n{attempt_number}:root"}),
                    root_capability=capability,
                ) from failure
            try:
                if temporary is not None and not temporary.closed:
                    temporary.close()
            except BaseException as cleanup_failure:
                self._retain_publication_resources(
                    failure,
                    cleanup_failure,
                    root_handle,
                    temporary,
                )
                raise _win32.Win32QuarantineRequired(
                    "analysis_adapter_root_cleanup_unproved",
                    handle=root_handle,
                    resources=(failure, cleanup_failure, temporary),
                ) from cleanup_failure
            raise

    def abort_adapter_tree(
        self,
        slot: AdapterTreeSlot,
        proof: AbortedAttemptTreeProof,
    ) -> None:
        """Remove one exact pre-marker aborted root and close its tmp parent."""

        self._require_exact_adapter_slot(slot, proof)
        if slot._root_capability.state != "aborted":
            _failure("analysis_adapter_tree_invalid")
        try:
            discard_aborted_attempt_root(
                proof,
                root_owner_token=self._session_token,
            )
            self._finish_removed_adapter_slot(slot)
        except (ProcessQuarantineRequired, _win32.Win32QuarantineRequired) as failure:
            slot._state = "quarantine"
            self._retain_publication_resources(failure, slot, proof)
            raise
        except BaseException as failure:
            slot._state = "quarantine"
            self._retain_publication_resources(failure, slot, proof)
            raise _win32.Win32QuarantineRequired(
                "analysis_adapter_abort_cleanup_unproved",
                handle=slot._root_handle,
                resources=(slot, proof),
            ) from failure

    def discard_adapter_tree(
        self,
        slot: AdapterTreeSlot,
        proof: AttemptTreeProof,
    ) -> DiscardedAttemptTreeProof:
        """Consume and remove one exact quiescent root under the live lease."""

        self._require_exact_adapter_slot(slot, proof)
        if slot._root_capability.state != "quiescent":
            _failure("analysis_adapter_tree_invalid")
        try:
            discarded = discard_attempt_tree(
                proof,
                root_owner_token=self._session_token,
            )
            prior = (slot._root_basename, slot._root_identity)
            self._finish_removed_adapter_slot(slot)
            if slot._attempt_number == 1:
                self._discarded_adapter_root = prior
            return discarded
        except (ProcessQuarantineRequired, _win32.Win32QuarantineRequired) as failure:
            slot._state = "quarantine"
            self._retain_publication_resources(failure, slot, proof)
            raise
        except BaseException as failure:
            slot._state = "quarantine"
            self._retain_publication_resources(failure, slot, proof)
            raise _win32.Win32QuarantineRequired(
                "analysis_adapter_discard_cleanup_unproved",
                handle=slot._root_handle,
                resources=(slot, proof),
            ) from failure

    def _open_publication_parents(
        self,
        *,
        include_temporary: bool,
    ) -> _PublicationParents:
        """Acquire destination parents before a temp, intent, or final read."""

        self._require_active()
        root = reports = rendered = temporary = None
        try:
            root = self._borrow_analysis_root()
            reports = _win32.open_relative_directory(
                root,
                self._paths.reports.name,
                _win32.DP,
                kind="analysis-reports-dp",
            )
            rendered = _win32.open_relative_directory(
                root,
                self._paths.rendered.name,
                _win32.DP,
                kind="analysis-rendered-dp",
            )
            if include_temporary:
                temporary = _win32.open_relative_directory(
                    root,
                    self._paths.temporary.name,
                    _win32.R0,
                    kind="analysis-temporary-parent",
                )
            for handle, basename in (
                (reports, self._paths.reports.name),
                (rendered, self._paths.rendered.name),
                (temporary, self._paths.temporary.name),
            ):
                if handle is None:
                    continue
                identity = _win32.query_handle_identity(handle)
                _AnalysisLeaseOwnership._prove_directory(
                    handle,
                    root,
                    basename,
                    identity,
                )
            self._prove_session_directories()
            return _PublicationParents(root, reports, rendered, temporary)
        except _win32.Win32QuarantineRequired as failure:
            raise _win32.Win32QuarantineRequired(
                "publication_parent_acquisition_unproved",
                handle=failure.handle,
                resources=(failure, temporary, rendered, reports, root),
            ) from failure
        except BaseException as failure:
            resources = tuple(
                item
                for item in (temporary, rendered, reports)
                if item is not None
            )
            try:
                for item in resources:
                    if not item.closed:
                        item.close()
            except _win32.Win32BoundaryError as close_failure:
                raise _win32.Win32QuarantineRequired(
                    "publication_parent_close_unproved",
                    handle=item,
                    resources=resources,
                ) from close_failure
            raise failure

    @staticmethod
    def _close_security_descriptor(security: Any) -> None:
        if security is not None and not security.closed:
            security.close()

    def _create_no_adapter_tree(
        self,
        *,
        parents: _PublicationParents,
        report: ValidatedAnalysisReport,
    ) -> _NoAdapterPublicationTree:
        """Create the sole session-owned offline tree and prove exact leaves."""

        if parents.temporary is None:
            _failure("analysis_publication_invalid")
        root_basename = ".taskgov-analysis-" + secrets.token_hex(4)
        root_handle = json_handle = markdown_handle = None
        json_identity = markdown_identity = None
        security = None
        try:
            before = self._temporary_root_inventory(parents.temporary)
            if len(before) >= ANALYSIS_MAX_QUARANTINE_ROOTS:
                _failure(
                    "analysis_too_large",
                    "analysis temporary root inventory is full",
                )
            security = _win32.ExplicitSecurityDescriptor.root()
            root_handle = _win32.create_relative_directory(
                parents.temporary,
                root_basename,
                security,
                kind="analysis-no-adapter-root",
            )
            self._close_security_descriptor(security)
            security = None
            root_identity = _win32.query_handle_identity(root_handle)
            after = self._temporary_root_inventory(parents.temporary)
            if (
                set(after) != set(before) | {root_basename}
                or after[root_basename].file_id != root_identity.file_id
                or any(after[name] != entry for name, entry in before.items())
            ):
                raise _win32.Win32BoundaryError()

            security = _win32.ExplicitSecurityDescriptor.report_temp()
            json_handle = _win32.create_relative_file(
                root_handle,
                "report.json",
                _win32.TH,
                security,
                kind="analysis-report-json-temp",
            )
            self._close_security_descriptor(security)
            security = None
            json_identity = _win32.write_flush_reread(
                json_handle,
                report.report_document,
                maximum=REPORT_JSON_MAX_BYTES,
            )

            security = _win32.ExplicitSecurityDescriptor.report_temp()
            markdown_handle = _win32.create_relative_file(
                root_handle,
                "report.md",
                _win32.TH,
                security,
                kind="analysis-report-markdown-temp",
            )
            self._close_security_descriptor(security)
            security = None
            markdown_identity = _win32.write_flush_reread(
                markdown_handle,
                report.markdown_bytes,
                maximum=REPORT_MARKDOWN_MAX_BYTES,
            )

            tree = _NoAdapterPublicationTree(
                root_handle=root_handle,
                root_parent=parents.temporary,
                root_basename=root_basename,
                json_leaf=_HeldPublicationLeaf(
                    json_handle,
                    json_identity,
                    root_handle,
                    "report.json",
                    parents.reports,
                    report.report_id + ".json",
                    report.report_document,
                    REPORT_JSON_MAX_BYTES,
                ),
                markdown_leaf=_HeldPublicationLeaf(
                    markdown_handle,
                    markdown_identity,
                    root_handle,
                    "report.md",
                    parents.rendered,
                    report.report_id + ".md",
                    report.markdown_bytes,
                    REPORT_MARKDOWN_MAX_BYTES,
                ),
            )
            tree.prove_exact()
            return tree
        except _win32.Win32QuarantineRequired as failure:
            raise _win32.Win32QuarantineRequired(
                "publication_tree_operation_unproved",
                handle=failure.handle,
                resources=(
                    failure,
                    parents,
                    security,
                    root_handle,
                    json_handle,
                    markdown_handle,
                    json_identity,
                    markdown_identity,
                ),
            ) from failure
        except BaseException as failure:
            # Everything here is pre-intent and fresh.  Delete only identities
            # obtained from the held handles, then prove the root absent.
            try:
                for handle, identity, basename in (
                    (markdown_handle, markdown_identity, "report.md"),
                    (json_handle, json_identity, "report.json"),
                ):
                    if handle is None or handle.closed:
                        continue
                    original = identity or _win32.query_handle_identity(handle)
                    _win32.rollback_relative_handle(
                        handle,
                        root_handle,
                        basename,
                        original=original,
                    )
                if root_handle is not None and not root_handle.closed:
                    _win32.prove_held_directory_empty(root_handle)
                    _win32.remove_relative_directory(
                        root_handle,
                        parents.temporary,
                        root_basename,
                    )
            except _win32.Win32QuarantineRequired:
                raise
            except _win32.Win32BoundaryError as cleanup_failure:
                raise _win32.Win32QuarantineRequired(
                    "publication_tree_cleanup_unproved",
                    handle=root_handle,
                    resources=(
                        parents,
                        json_handle,
                        markdown_handle,
                        json_identity,
                        markdown_identity,
                    ),
                ) from cleanup_failure
            raise failure
        finally:
            try:
                self._close_security_descriptor(security)
            except _win32.Win32BoundaryError as close_failure:
                raise _win32.Win32QuarantineRequired(
                    "publication_security_cleanup_unproved",
                    handle=root_handle,
                    resources=(
                        parents,
                        security,
                        json_handle,
                        markdown_handle,
                        json_identity,
                        markdown_identity,
                    ),
                ) from close_failure

    def _populate_adapter_report_tree(
        self,
        *,
        tree: _AdapterPublicationTree,
        parents: _PublicationParents,
        slot: AdapterTreeSlot,
        report: ValidatedAnalysisReport,
    ) -> None:
        """Create only report THs in the process-proved borrowed root."""

        if parents.temporary is not None or tree.borrowed_root.root_handle is not slot._root_handle:
            _failure("analysis_publication_invalid")
        root_handle = tree.borrowed_root.root_handle
        json_handle = markdown_handle = None
        json_identity = markdown_identity = None
        security = None
        try:
            security = _win32.ExplicitSecurityDescriptor.report_temp()
            json_handle = _win32.create_relative_file(
                root_handle,
                "report.json",
                _win32.TH,
                security,
                kind="analysis-adapter-report-json-temp",
            )
            self._close_security_descriptor(security)
            security = None
            json_identity = _win32.write_flush_reread(
                json_handle,
                report.report_document,
                maximum=REPORT_JSON_MAX_BYTES,
            )
            tree.json_leaf = _HeldPublicationLeaf(
                json_handle,
                json_identity,
                root_handle,
                "report.json",
                parents.reports,
                report.report_id + ".json",
                report.report_document,
                REPORT_JSON_MAX_BYTES,
            )

            security = _win32.ExplicitSecurityDescriptor.report_temp()
            markdown_handle = _win32.create_relative_file(
                root_handle,
                "report.md",
                _win32.TH,
                security,
                kind="analysis-adapter-report-markdown-temp",
            )
            self._close_security_descriptor(security)
            security = None
            markdown_identity = _win32.write_flush_reread(
                markdown_handle,
                report.markdown_bytes,
                maximum=REPORT_MARKDOWN_MAX_BYTES,
            )
            tree.markdown_leaf = _HeldPublicationLeaf(
                markdown_handle,
                markdown_identity,
                root_handle,
                "report.md",
                parents.rendered,
                report.report_id + ".md",
                report.markdown_bytes,
                REPORT_MARKDOWN_MAX_BYTES,
            )
            tree.prove_exact()
        except (_win32.Win32QuarantineRequired, ProcessQuarantineRequired):
            raise
        except BaseException as failure:
            try:
                for handle, identity, basename, installed in (
                    (
                        markdown_handle,
                        markdown_identity,
                        "report.md",
                        tree.markdown_leaf is not None,
                    ),
                    (
                        json_handle,
                        json_identity,
                        "report.json",
                        tree.json_leaf is not None,
                    ),
                ):
                    if installed or handle is None or handle.closed:
                        continue
                    original = identity or _win32.query_handle_identity(handle)
                    _win32.rollback_relative_handle(
                        handle,
                        root_handle,
                        basename,
                        original=original,
                    )
                tree.rollback(
                    owner_token=self._session_token,
                    binding=slot._binding,
                    attempt_number=slot._attempt_number,
                )
            except BaseException as cleanup_failure:
                raise _win32.Win32QuarantineRequired(
                    "analysis_adapter_report_cleanup_unproved",
                    handle=root_handle,
                    resources=(
                        failure,
                        cleanup_failure,
                        tree,
                        parents,
                        slot,
                    ),
                ) from cleanup_failure
            raise
        finally:
            if security is not None and not security.closed:
                try:
                    security.close()
                except _win32.Win32BoundaryError as close_failure:
                    raise _win32.Win32QuarantineRequired(
                        "analysis_adapter_report_security_cleanup_unproved",
                        handle=root_handle,
                        resources=(security, tree, parents, slot),
                    ) from close_failure

    def _rollback_adapter_publication(
        self,
        tree: _AdapterPublicationTree,
        slot: AdapterTreeSlot,
    ) -> None:
        tree.rollback(
            owner_token=self._session_token,
            binding=slot._binding,
            attempt_number=slot._attempt_number,
        )
        if self._adapter_slot is slot:
            self._finish_removed_adapter_slot(slot)

    @staticmethod
    def _failed_status(intent: dict[str, Any], *, fixed_code: str) -> dict[str, Any]:
        failed = deepcopy(intent)
        failed.update(
            {
                "state": "failed",
                "fixed_code": fixed_code,
                "report_id": None,
                "report_digest": None,
                "render_digest": None,
            }
        )
        return failed

    @staticmethod
    def _close_recovery_leaf(handle: Any, *resources: object) -> None:
        try:
            handle.close()
        except _win32.Win32BoundaryError as close_failure:
            raise _win32.Win32QuarantineRequired(
                "recovery_leaf_close_unproved",
                handle=handle,
                resources=resources,
            ) from close_failure

    def _finish_publication_release(self) -> None:
        self._publication_active = False
        self.release_normal()

    def _retain_after_uncertain_status_write(
        self,
        descriptor: dict[str, Any],
        *resources: object,
    ) -> PublicationResult:
        """Retain everything and report only a held-lease status observation."""

        try:
            observed_status = self.read_bound_job(descriptor).status
            observed_failure = None
        except BaseException as observe_failure:
            observed_status = None
            observed_failure = observe_failure
        self._retain_publication_resources(
            *resources,
            observed_failure,
        )
        return PublicationResult(
            observed_status,
            "deferred",
            lease_retained=True,
        )

    def _finish_preintent_failure(
        self,
        *,
        descriptor: dict[str, Any],
        current: dict[str, Any],
        failure: BaseException,
        parents: _PublicationParents | None,
        report_invalid: bool,
    ) -> PublicationResult:
        """Close pre-intent parents, then classify one deterministic failure."""

        if parents is not None:
            try:
                parents.close()
            except BaseException as cleanup_failure:
                self._retain_publication_resources(
                    failure,
                    cleanup_failure,
                    parents,
                )
                return PublicationResult(current, "deferred", lease_retained=True)
        if not report_invalid:
            self._finish_publication_release()
            return PublicationResult(current, "deferred")
        failed = self._failed_status(current, fixed_code="report_invalid")
        try:
            failed_result = self.cas_status(
                descriptor=descriptor,
                expected_status=current,
                status=failed,
            )
        except _win32.Win32QuarantineRequired as status_failure:
            return self._retain_after_uncertain_status_write(
                descriptor,
                failure,
                status_failure,
            )
        except BaseException as status_failure:
            self._retain_publication_resources(failure, status_failure)
            return PublicationResult(current, "deferred", lease_retained=True)
        self._finish_publication_release()
        return PublicationResult(
            failed_result.status,
            "failed" if failed_result.applied else "deferred",
        )

    def _enqueue_descriptor(self, proposed: object) -> EnqueueResult:
        self._require_active()
        try:
            normalized = validate_descriptor(proposed)
        except AnalysisContractError as exc:
            raise AnalysisOutboxError(
                "source_invalid",
                "analysis source is invalid",
            ) from exc
        job_name = f"{normalized['analysis_job_id']}.json"
        self._prove_session_directories()
        existing = _read_descriptor(
            self._directories.outbox,
            job_name,
            missing_allowed=True,
        )
        if existing is None and _relative_leaf_present(
            self._directories.status,
            job_name,
            maximum=ANALYSIS_STATUS_MAX_BYTES,
        ):
            _failure("analysis_collision", "analysis job collides with existing state")
        if existing is not None:
            if not descriptor_replay_matches(existing, normalized):
                _failure("analysis_collision", "analysis job collides with existing state")
            status = _read_status(
                self._directories.status,
                job_name,
                descriptor=existing,
                missing_allowed=True,
            )
            if status is None:
                status = pending_status(existing)
                _create_relative_durable_file(
                    self._directories.status,
                    job_name,
                    canonical_json_document_bytes(status),
                    maximum=ANALYSIS_STATUS_MAX_BYTES,
                    kind="analysis-status-th",
                )
            self._prove_session_directories()
            return EnqueueResult(existing, status, True)

        status = pending_status(normalized)
        _create_relative_durable_file(
            self._directories.outbox,
            job_name,
            canonical_json_document_bytes(normalized),
            maximum=ANALYSIS_DESCRIPTOR_MAX_BYTES,
            kind="analysis-descriptor-th",
        )
        # A valid descriptor without status is intentionally recoverable by
        # the next locked enqueue; never remove or rewrite it here.
        _create_relative_durable_file(
            self._directories.status,
            job_name,
            canonical_json_document_bytes(status),
            maximum=ANALYSIS_STATUS_MAX_BYTES,
            kind="analysis-status-th",
        )
        self._prove_session_directories()
        return EnqueueResult(normalized, status, False)

    def cas_status(
        self,
        *,
        descriptor: object,
        expected_status: object,
        status: object,
    ) -> StatusCasResult:
        """Compare-and-transition status once without reacquiring the lease."""

        self._require_active()
        try:
            bound = validate_descriptor(descriptor)
            expected = validate_status(expected_status, descriptor=bound)
            normalized = validate_status(status, descriptor=bound)
        except AnalysisContractError as exc:
            raise AnalysisOutboxError(
                "analysis_outbox_invalid",
                "analysis outbox could not be changed safely",
            ) from exc
        job = self.read_bound_job(bound)
        stored = job.status
        if stored != expected:
            _failure("analysis_collision", "analysis job collides with existing state")
        try:
            validate_status_transition(stored, normalized, descriptor=bound)
        except AnalysisContractError as exc:
            raise AnalysisOutboxError(
                "analysis_outbox_invalid",
                "analysis outbox could not be changed safely",
            ) from exc
        if stored == normalized:
            return StatusCasResult(normalized, "unchanged")

        status_basename = f"{bound['analysis_job_id']}.json"
        temporary_basename = ".taskgov-analysis-status-" + secrets.token_hex(4) + ".tmp"
        status_bytes = canonical_json_document_bytes(normalized)
        self._prove_session_directories()
        temporary = _create_relative_durable_file(
            self._directories.status,
            temporary_basename,
            status_bytes,
            maximum=ANALYSIS_STATUS_MAX_BYTES,
            kind="analysis-status-temp-th",
            retain_handle=True,
        )
        self._prove_session_directories()

        replacement_applied = False
        cleanup_authorized = True
        try:
            try:
                self._prove_session_directories()
                replaced = _win32.replace_relative_file(
                    temporary.handle,
                    self._directories.status,
                    temporary_basename,
                    status_basename,
                )
            except _win32.Win32QuarantineRequired:
                cleanup_authorized = False
                raise
            except _win32.Win32BoundaryError as replace_error:
                if replace_error.code != "analysis_replace_not_applied":
                    cleanup_authorized = False
                    raise _win32.Win32QuarantineRequired(
                        "analysis_status_replace_unproved",
                        handle=temporary.handle,
                        resources=(
                            self._directories.status,
                            temporary,
                            replace_error,
                        ),
                    ) from replace_error
                source = _win32.prove_held_membership(
                    temporary.handle,
                    self._directories.status,
                    temporary_basename,
                )
                if not temporary.identity.same_object(source):
                    cleanup_authorized = False
                    raise _win32.Win32QuarantineRequired(
                        "analysis_status_replace_source_unproved",
                        handle=temporary.handle,
                        resources=(self._directories.status, temporary),
                    )
                observed = _read_status(
                    self._directories.status,
                    status_basename,
                    descriptor=bound,
                )
                self._prove_session_directories()
                if observed == stored:
                    return StatusCasResult(stored, "ambiguous_not_applied")
                raise AnalysisOutboxError(
                    "analysis_collision",
                    "analysis job collides with existing state",
                ) from replace_error
            except BaseException as replace_failure:
                cleanup_authorized = False
                raise _win32.Win32QuarantineRequired(
                    "analysis_status_replace_unproved",
                    handle=temporary.handle,
                    resources=(
                        self._directories.status,
                        temporary,
                        replace_failure,
                    ),
                ) from replace_failure
            temporary.basename = status_basename
            replacement_applied = True
            try:
                if not temporary.identity.same_object(replaced):
                    raise _win32.Win32BoundaryError()
                observed = _status_from_held_durable_file(
                    self._directories.status,
                    temporary,
                    descriptor=bound,
                )
                if observed != normalized:
                    raise _win32.Win32BoundaryError()
                self._prove_session_directories()
                _close_relative_durable_file(self._directories.status, temporary)
            except _win32.Win32QuarantineRequired:
                cleanup_authorized = False
                raise
            except BaseException as postcondition_failure:
                cleanup_authorized = False
                raise _win32.Win32QuarantineRequired(
                    "analysis_status_replace_postcondition_unproved",
                    handle=temporary.handle,
                    resources=(
                        self._directories.status,
                        temporary,
                        postcondition_failure,
                    ),
                ) from postcondition_failure
            temporary = None
            self._prove_session_directories()
            return StatusCasResult(normalized, "replaced")
        finally:
            if (
                temporary is not None
                and cleanup_authorized
                and not replacement_applied
            ):
                _cleanup_relative_durable_file(self._directories.status, temporary)
            if not replacement_applied and cleanup_authorized:
                self._prove_session_directories()

    def publish_no_adapter(
        self,
        *,
        descriptor: object,
        expected_status: object,
        packet: AnalysisPacket,
        report: ValidatedAnalysisReport,
        proof: NoAdapterControllerProof,
        prompt_digest: str | None = None,
    ) -> PublicationResult:
        """Publish one exact offline or optional no-call report."""

        self._require_active()
        # This in-memory capability is consumed before any publication parent,
        # temporary leaf, status intent, or destination can be changed.
        self._consume_no_adapter_controller_proof(proof)
        try:
            bound = validate_descriptor(descriptor)
            current = validate_status(expected_status, descriptor=bound)
        except AnalysisContractError as exc:
            raise AnalysisOutboxError(
                "analysis_publication_invalid",
                "analysis publication input is invalid",
            ) from exc
        self._publication_active = True
        parents: _PublicationParents | None = None
        tree: _NoAdapterPublicationTree | None = None
        intent: dict[str, Any] | None = None
        try:
            job = self.read_bound_job(bound)
            if job.status != current:
                _failure(
                    "analysis_collision",
                    "analysis job collides with existing state",
                )
            if (
                current["state"] != "running"
                or current["adapter_attempt_count"] != 0
                or current["accepted_output_digest"] is not None
                or any(
                    current[name] is not None
                    for name in ("report_id", "report_digest", "render_digest")
                )
            ):
                _failure("analysis_publication_invalid")
            if (
                not isinstance(packet, AnalysisPacket)
                or not isinstance(report, ValidatedAnalysisReport)
                or current["packet_digest"] != packet.packet_digest
            ):
                return self._finish_preintent_failure(
                    descriptor=bound,
                    current=current,
                    failure=AnalysisValidationError(
                        "report_invalid",
                        "analysis report is invalid",
                    ),
                    parents=None,
                    report_invalid=True,
                )
            mode = bound["recipe"]["inference_mode"]
            inference_state = current["inference_state"]
            if mode == "offline":
                if inference_state != "disabled" or prompt_digest is not None:
                    _failure("analysis_publication_invalid")
            elif mode == "codex_optional":
                if (
                    inference_state not in {"policy_blocked", "input_too_large"}
                ):
                    _failure("analysis_publication_invalid")
                if prompt_digest != FIXED_PROMPT_DIGEST:
                    return self._finish_preintent_failure(
                        descriptor=bound,
                        current=current,
                        failure=AnalysisValidationError(
                            "report_invalid",
                            "analysis report is invalid",
                        ),
                        parents=None,
                        report_invalid=True,
                    )
            else:
                _failure("analysis_publication_invalid")
            try:
                validated = validate_report_document(
                    report.report_document,
                    descriptor=bound,
                    packet=packet,
                    inference_state=inference_state,
                    prompt_digest=prompt_digest,
                )
            except AnalysisValidationError as validation_failure:
                return self._finish_preintent_failure(
                    descriptor=bound,
                    current=current,
                    failure=validation_failure,
                    parents=None,
                    report_invalid=True,
                )
            if validated != report:
                return self._finish_preintent_failure(
                    descriptor=bound,
                    current=current,
                    failure=AnalysisValidationError(
                        "report_invalid",
                        "analysis report is invalid",
                    ),
                    parents=None,
                    report_invalid=True,
                )

            # Both DPs precede the fresh root, both THs, and publication intent.
            parents = self._open_publication_parents(include_temporary=True)
            tree = self._create_no_adapter_tree(parents=parents, report=validated)

            # Validate the bytes actually bound to the held THs, then reprove
            # the no-adapter exact two-leaf tree immediately before intent.
            held_report = validate_report_document(
                tree.json_leaf.content,
                descriptor=bound,
                packet=packet,
                inference_state=inference_state,
                prompt_digest=prompt_digest,
            )
            if (
                held_report != validated
                or tree.markdown_leaf.content != held_report.markdown_bytes
            ):
                raise AnalysisValidationError(
                    "report_invalid",
                    "analysis report is invalid",
                )
            tree.prove_exact()

            intent = deepcopy(current)
            intent.update(
                {
                    "report_id": held_report.report_id,
                    "report_digest": held_report.report_digest,
                    "render_digest": held_report.render_digest,
                }
            )
            intent_result = self.cas_status(
                descriptor=bound,
                expected_status=current,
                status=intent,
            )
            if not intent_result.applied:
                tree.rollback()
                tree = None
                parents.close()
                parents = None
                self._finish_publication_release()
                return PublicationResult(intent_result.status, "deferred")
            current = intent_result.status

            # Exact intent order is JSON, then Markdown.
            tree.json_leaf.promote()
            tree.markdown_leaf.promote()
            tree.remove_empty_root()
            published = deepcopy(current)
            published.update({"state": "published", "fixed_code": None})
            published_result = self.cas_status(
                descriptor=bound,
                expected_status=current,
                status=published,
            )
            if not published_result.applied:
                # The reread proved the old running intent.  Roll back only
                # matching held identities, then use the ordinary failure CAS.
                tree.rollback()
                tree = None
                failed = self._failed_status(current, fixed_code="publication_failed")
                failed_result = self.cas_status(
                    descriptor=bound,
                    expected_status=current,
                    status=failed,
                )
                if failed_result.applied:
                    current = failed_result.status
                    parents.close()
                    parents = None
                    self._finish_publication_release()
                    return PublicationResult(current, "failed")
                self._retain_publication_resources(
                    published_result,
                    failed_result,
                    parents,
                )
                return PublicationResult(
                    failed_result.status,
                    "deferred",
                    lease_retained=True,
                )

            # Successful status publication precedes every held final close.
            current = published_result.status
            tree.json_leaf.close_published()
            tree.markdown_leaf.close_published()
            tree = None
            parents.close()
            parents = None
            self._finish_publication_release()
            return PublicationResult(published_result.status, "published")
        except _AnalysisLeaseReleaseUncertain:
            self._publication_active = False
            raise
        except _win32.Win32QuarantineRequired as failure:
            return self._retain_after_uncertain_status_write(
                bound,
                failure,
                tree,
                parents,
            )
        except BaseException as failure:
            if not isinstance(failure, Exception):
                self._retain_publication_resources(failure, tree, parents)
                raise
            # A post-intent rollback is authorized only after the held lease
            # reread proves that the exact running intent is still current.
            if intent is not None:
                try:
                    observed = self.read_bound_job(bound).status
                except BaseException as observe_failure:
                    self._retain_publication_resources(
                        failure,
                        observe_failure,
                        tree,
                        parents,
                    )
                    return PublicationResult(current, "deferred", lease_retained=True)
                if observed != current or observed != intent:
                    self._retain_publication_resources(failure, tree, parents, observed)
                    return PublicationResult(observed, "deferred", lease_retained=True)
            try:
                if tree is not None:
                    tree.rollback()
                    tree = None
            except BaseException as cleanup_failure:
                self._retain_publication_resources(
                    failure,
                    cleanup_failure,
                    tree,
                    parents,
                )
                return PublicationResult(current, "deferred", lease_retained=True)

            if intent is None:
                result = self._finish_preintent_failure(
                    descriptor=bound,
                    current=current,
                    failure=failure,
                    parents=parents,
                    report_invalid=isinstance(failure, AnalysisValidationError),
                )
                parents = None
                return result
            failed = self._failed_status(current, fixed_code="publication_failed")
            try:
                failed_result = self.cas_status(
                    descriptor=bound,
                    expected_status=current,
                    status=failed,
                )
            except _win32.Win32QuarantineRequired as status_failure:
                return self._retain_after_uncertain_status_write(
                    bound,
                    failure,
                    status_failure,
                    parents,
                )
            except BaseException as status_failure:
                self._retain_publication_resources(
                    failure,
                    status_failure,
                    parents,
                )
                return PublicationResult(current, "deferred", lease_retained=True)
            if failed_result.applied:
                current = failed_result.status
                if parents is not None:
                    try:
                        parents.close()
                        parents = None
                    except BaseException as cleanup_failure:
                        self._retain_publication_resources(
                            failure,
                            cleanup_failure,
                            parents,
                        )
                        return PublicationResult(
                            current,
                            "deferred",
                            lease_retained=True,
                        )
                self._finish_publication_release()
                return PublicationResult(current, "failed")
            self._retain_publication_resources(
                failure,
                failed_result,
                parents,
            )
            return PublicationResult(
                failed_result.status,
                "deferred",
                lease_retained=True,
            )

    def publish_adapter(
        self,
        *,
        descriptor: object,
        expected_status: object,
        packet: AnalysisPacket,
        report: ValidatedAnalysisReport,
        slot: AdapterTreeSlot,
        tree_proof: AttemptTreeProof,
        adapter_output: ValidatedAdapterOutput | None,
        prompt_digest: str,
    ) -> PublicationResult:
        """Publish one adapter report from its exact process-proved root."""

        self._require_active()
        self._require_exact_adapter_slot(slot, tree_proof)
        if (
            type(tree_proof) is not AttemptTreeProof
            or slot._root_capability.state != "quiescent"
        ):
            _failure("analysis_adapter_tree_invalid")
        try:
            bound = validate_descriptor(descriptor)
            current = validate_status(expected_status, descriptor=bound)
        except AnalysisContractError as exc:
            raise AnalysisOutboxError(
                "analysis_publication_invalid",
                "analysis publication input is invalid",
            ) from exc
        job = self.read_bound_job(bound)
        if job.status != current:
            _failure(
                "analysis_collision",
                "analysis job collides with existing state",
            )

        self._publication_active = True
        parents: _PublicationParents | None = None
        tree: _AdapterPublicationTree | None = None
        intent: dict[str, Any] | None = None
        try:
            try:
                normalized_packet = revalidate_analysis_packet(packet, bound)
                expected_binding = MockBinding(
                    analysis_job_id=bound["analysis_job_id"],
                    source_key=bound["source_key"],
                    recipe_digest=bound["recipe_digest"],
                    packet_digest=normalized_packet.packet_digest,
                )
                inference_state = current["inference_state"]
                succeeded = inference_state == "succeeded"
                if (
                    bound["recipe"]["inference_mode"] != "codex_optional"
                    or slot._binding != expected_binding
                    or slot._attempt_number != current["adapter_attempt_count"]
                    or slot._attempt_number not in {1, 2}
                    or current["state"] != "running"
                    or current["packet_digest"] != normalized_packet.packet_digest
                    or inference_state not in ATTEMPT_OUTCOME_INFERENCE_STATES
                    or prompt_digest != FIXED_PROMPT_DIGEST
                    or type(report) is not ValidatedAnalysisReport
                    or any(
                        current[name] is not None
                        for name in ("report_id", "report_digest", "render_digest")
                    )
                    or succeeded != (type(adapter_output) is ValidatedAdapterOutput)
                    or (
                        succeeded
                        and current["accepted_output_digest"]
                        != adapter_output.accepted_output_digest
                    )
                    or (
                        not succeeded
                        and (
                            adapter_output is not None
                            or current["accepted_output_digest"] is not None
                        )
                    )
                ):
                    raise AnalysisValidationError(
                        "report_invalid",
                        "analysis report is invalid",
                    )
                validated = validate_report_document(
                    report.report_document,
                    descriptor=bound,
                    packet=normalized_packet,
                    inference_state=inference_state,
                    adapter_output=adapter_output,
                    prompt_digest=prompt_digest,
                )
                if validated != report:
                    raise AnalysisValidationError(
                        "report_invalid",
                        "analysis report is invalid",
                    )
            except (AnalysisPacketError, AnalysisValidationError) as validation_failure:
                try:
                    self.discard_adapter_tree(slot, tree_proof)
                except BaseException as cleanup_failure:
                    self._retain_publication_resources(
                        validation_failure,
                        cleanup_failure,
                        slot,
                        tree_proof,
                    )
                    return PublicationResult(
                        current,
                        "deferred",
                        lease_retained=True,
                    )
                return self._finish_preintent_failure(
                    descriptor=bound,
                    current=current,
                    failure=validation_failure,
                    parents=None,
                    report_invalid=True,
                )

            # The slot already owns tmp R0 with share=0.  Acquire both DPs,
            # then consume and borrow the exact empty process root.
            parents = self._open_publication_parents(include_temporary=False)
            ready = consume_attempt_tree_for_publication(
                tree_proof,
                root_capability=slot._root_capability,
                root_owner_token=self._session_token,
                binding=slot._binding,
            )
            borrowed = borrow_publish_ready_attempt_root(
                ready,
                root_owner_token=self._session_token,
                binding=slot._binding,
                attempt_number=slot._attempt_number,
            )
            tree = _AdapterPublicationTree(borrowed_root=borrowed)
            self._populate_adapter_report_tree(
                tree=tree,
                parents=parents,
                slot=slot,
                report=validated,
            )

            held_report = validate_report_document(
                tree.json_leaf.content,
                descriptor=bound,
                packet=normalized_packet,
                inference_state=inference_state,
                adapter_output=adapter_output,
                prompt_digest=prompt_digest,
            )
            if (
                held_report != validated
                or tree.markdown_leaf.content != held_report.markdown_bytes
            ):
                raise AnalysisValidationError(
                    "report_invalid",
                    "analysis report is invalid",
                )
            tree.prove_exact()

            intent = deepcopy(current)
            intent.update(
                {
                    "report_id": held_report.report_id,
                    "report_digest": held_report.report_digest,
                    "render_digest": held_report.render_digest,
                }
            )
            intent_result = self.cas_status(
                descriptor=bound,
                expected_status=current,
                status=intent,
            )
            if not intent_result.applied:
                self._rollback_adapter_publication(tree, slot)
                tree = None
                parents.close()
                parents = None
                self._finish_publication_release()
                return PublicationResult(intent_result.status, "deferred")
            current = intent_result.status

            tree.json_leaf.promote()
            tree.markdown_leaf.promote()
            tree.remove_empty_root(
                owner_token=self._session_token,
                binding=slot._binding,
                attempt_number=slot._attempt_number,
            )
            self._finish_removed_adapter_slot(slot)
            published = deepcopy(current)
            published.update({"state": "published", "fixed_code": None})
            published_result = self.cas_status(
                descriptor=bound,
                expected_status=current,
                status=published,
            )
            if not published_result.applied:
                self._rollback_adapter_publication(tree, slot)
                tree = None
                failed = self._failed_status(
                    current,
                    fixed_code="publication_failed",
                )
                failed_result = self.cas_status(
                    descriptor=bound,
                    expected_status=current,
                    status=failed,
                )
                if failed_result.applied:
                    current = failed_result.status
                    parents.close()
                    parents = None
                    self._finish_publication_release()
                    return PublicationResult(current, "failed")
                self._retain_publication_resources(
                    published_result,
                    failed_result,
                    parents,
                )
                return PublicationResult(
                    failed_result.status,
                    "deferred",
                    lease_retained=True,
                )

            # Persist the terminal observation before any fallible final close.
            current = published_result.status
            tree.json_leaf.close_published()
            tree.markdown_leaf.close_published()
            tree = None
            parents.close()
            parents = None
            self._finish_publication_release()
            return PublicationResult(current, "published")
        except _AnalysisLeaseReleaseUncertain:
            self._publication_active = False
            raise
        except ProcessQuarantineRequired as failure:
            if self._adapter_slot is slot and slot._state == "active":
                slot._state = "quarantine"
            self._retain_publication_resources(failure, tree, parents, slot)
            return PublicationResult(current, "deferred", lease_retained=True)
        except _win32.Win32QuarantineRequired as failure:
            if self._adapter_slot is slot and slot._state == "active":
                slot._state = "quarantine"
            return self._retain_after_uncertain_status_write(
                bound,
                failure,
                tree,
                parents,
                slot,
            )
        except BaseException as failure:
            if not isinstance(failure, Exception):
                self._retain_publication_resources(failure, tree, parents, slot)
                raise
            if intent is not None:
                try:
                    observed = self.read_bound_job(bound).status
                except BaseException as observe_failure:
                    self._retain_publication_resources(
                        failure,
                        observe_failure,
                        tree,
                        parents,
                        slot,
                    )
                    return PublicationResult(
                        current,
                        "deferred",
                        lease_retained=True,
                    )
                if observed != current or observed != intent:
                    self._retain_publication_resources(
                        failure,
                        tree,
                        parents,
                        slot,
                        observed,
                    )
                    return PublicationResult(
                        observed,
                        "deferred",
                        lease_retained=True,
                    )
            try:
                if tree is not None:
                    self._rollback_adapter_publication(tree, slot)
                    tree = None
                elif self._adapter_slot is slot:
                    if slot._root_capability.state == "quiescent":
                        self.discard_adapter_tree(slot, tree_proof)
                    elif slot._root_capability.state == "removed":
                        self._finish_removed_adapter_slot(slot)
                    else:
                        _failure("analysis_adapter_tree_invalid")
            except BaseException as cleanup_failure:
                if self._adapter_slot is slot and slot._state == "active":
                    slot._state = "quarantine"
                self._retain_publication_resources(
                    failure,
                    cleanup_failure,
                    tree,
                    parents,
                    slot,
                )
                return PublicationResult(
                    current,
                    "deferred",
                    lease_retained=True,
                )

            if intent is None:
                result = self._finish_preintent_failure(
                    descriptor=bound,
                    current=current,
                    failure=failure,
                    parents=parents,
                    report_invalid=isinstance(failure, AnalysisValidationError),
                )
                parents = None
                return result
            failed = self._failed_status(current, fixed_code="publication_failed")
            try:
                failed_result = self.cas_status(
                    descriptor=bound,
                    expected_status=current,
                    status=failed,
                )
            except _win32.Win32QuarantineRequired as status_failure:
                return self._retain_after_uncertain_status_write(
                    bound,
                    failure,
                    status_failure,
                    parents,
                    slot,
                )
            except BaseException as status_failure:
                self._retain_publication_resources(
                    failure,
                    status_failure,
                    parents,
                )
                return PublicationResult(current, "deferred", lease_retained=True)
            if failed_result.applied:
                current = failed_result.status
                if parents is not None:
                    try:
                        parents.close()
                        parents = None
                    except BaseException as cleanup_failure:
                        self._retain_publication_resources(
                            failure,
                            cleanup_failure,
                            parents,
                        )
                        return PublicationResult(
                            current,
                            "deferred",
                            lease_retained=True,
                        )
                self._finish_publication_release()
                return PublicationResult(current, "failed")
            self._retain_publication_resources(
                failure,
                failed_result,
                parents,
            )
            return PublicationResult(
                failed_result.status,
                "deferred",
                lease_retained=True,
            )

    def recover_publication(
        self,
        *,
        descriptor: object,
        expected_status: object,
        packet: AnalysisPacket,
        expected_prompt_digest: str | None = None,
    ) -> PublicationResult:
        """Recover exact intent-bound finals without opening private output."""

        self._require_active()
        try:
            bound = validate_descriptor(descriptor)
            current = validate_status(expected_status, descriptor=bound)
        except AnalysisContractError as exc:
            raise AnalysisOutboxError(
                "analysis_publication_invalid",
                "analysis recovery input is invalid",
            ) from exc
        if (
            not isinstance(packet, AnalysisPacket)
            or current["state"] != "running"
            or current["packet_digest"] != packet.packet_digest
            or not all(
                current[name] is not None
                for name in ("report_id", "report_digest", "render_digest")
            )
        ):
            _failure("analysis_publication_invalid")
        mode = bound["recipe"]["inference_mode"]
        if mode == "offline":
            if (
                current["inference_state"] != "disabled"
                or current["accepted_output_digest"] is not None
                or expected_prompt_digest is not None
            ):
                _failure("analysis_publication_invalid")
        elif mode == "codex_optional":
            if (
                type(expected_prompt_digest) is not str
                or _SEALED_DIGEST.fullmatch(expected_prompt_digest) is None
            ):
                _failure("analysis_publication_invalid")
        else:
            _failure("analysis_publication_invalid")
        job = self.read_bound_job(bound)
        if job.status != current:
            _failure("analysis_collision", "analysis job collides with existing state")

        self._publication_active = True
        parents: _PublicationParents | None = None
        json_handle = markdown_handle = None
        json_original = markdown_original = None
        matching_json = matching_markdown = False
        changed_destination = False
        try:
            # Recovery acquires both destination parents before either exact RH.
            # It never opens or enumerates the private temporary namespace.
            parents = self._open_publication_parents(include_temporary=False)
            report_basename = current["report_id"] + ".json"
            markdown_basename = current["report_id"] + ".md"
            json_handle = _win32.open_relative_file_if_present(
                parents.reports,
                report_basename,
                maximum=REPORT_JSON_MAX_BYTES,
                kind="analysis-recovery-report",
            )
            markdown_handle = _win32.open_relative_file_if_present(
                parents.rendered,
                markdown_basename,
                maximum=REPORT_MARKDOWN_MAX_BYTES,
                kind="analysis-recovery-markdown",
            )

            recovered: ValidatedAnalysisReport | None = None
            json_mismatch = False
            markdown_mismatch = False
            if json_handle is not None:
                json_original = _win32.prove_held_membership(
                    json_handle,
                    parents.reports,
                    report_basename,
                )
                document = _win32.read_handle_capped(
                    json_handle,
                    maximum=REPORT_JSON_MAX_BYTES,
                )
                try:
                    recovered = validate_recovery_report_document(
                        document,
                        descriptor=bound,
                        packet=packet,
                        inference_state=current["inference_state"],
                        accepted_output_digest=current["accepted_output_digest"],
                        expected_prompt_digest=expected_prompt_digest,
                        expected_report_id=current["report_id"],
                        expected_report_digest=current["report_digest"],
                        expected_render_digest=current["render_digest"],
                    )
                except AnalysisValidationError:
                    json_mismatch = True
                else:
                    matching_json = True
                    if not json_original.same_object(
                        _win32.prove_held_membership(
                            json_handle,
                            parents.reports,
                            report_basename,
                        )
                    ):
                        _failure("analysis_publication_invalid")

            if markdown_handle is not None:
                markdown_original = _win32.prove_held_membership(
                    markdown_handle,
                    parents.rendered,
                    markdown_basename,
                )
                markdown = _win32.read_handle_capped(
                    markdown_handle,
                    maximum=REPORT_MARKDOWN_MAX_BYTES,
                )
                if recovered is not None and markdown == recovered.markdown_bytes:
                    matching_markdown = True
                    if not markdown_original.same_object(
                        _win32.prove_held_membership(
                            markdown_handle,
                            parents.rendered,
                            markdown_basename,
                        )
                    ):
                        _failure("analysis_publication_invalid")
                else:
                    markdown_mismatch = True

            if matching_json and matching_markdown:
                published = deepcopy(current)
                published.update({"state": "published", "fixed_code": None})
                try:
                    published_result = self.cas_status(
                        descriptor=bound,
                        expected_status=current,
                        status=published,
                    )
                except BaseException as status_failure:
                    if not isinstance(status_failure, Exception):
                        self._retain_publication_resources(
                            status_failure,
                            parents,
                            json_handle,
                            markdown_handle,
                        )
                        raise
                    try:
                        observed = self.read_bound_job(bound).status
                    except BaseException as observe_failure:
                        self._retain_publication_resources(
                            status_failure,
                            observe_failure,
                            parents,
                            json_handle,
                            markdown_handle,
                        )
                        return PublicationResult(
                            current,
                            "deferred",
                            lease_retained=True,
                        )
                    if observed == published:
                        published_result = StatusCasResult(
                            observed,
                            "ambiguous_applied",
                        )
                    elif observed == current:
                        published_result = StatusCasResult(
                            observed,
                            "ambiguous_not_applied",
                        )
                    else:
                        self._retain_publication_resources(
                            status_failure,
                            parents,
                            json_handle,
                            markdown_handle,
                            observed,
                        )
                        return PublicationResult(
                            observed,
                            "deferred",
                            lease_retained=True,
                        )
                self._close_recovery_leaf(json_handle, parents)
                json_handle = None
                self._close_recovery_leaf(markdown_handle, parents)
                markdown_handle = None
                parents.close()
                parents = None
                self._finish_publication_release()
                if published_result.applied:
                    return PublicationResult(published_result.status, "published")
                return PublicationResult(published_result.status, "deferred")

            # Roll back only independently authenticated matching bytes.  A
            # mismatched/foreign peer stays held but unchanged until its close.
            if matching_markdown:
                marked = _win32.set_delete_disposition(
                    markdown_handle,
                    delete=True,
                )
                changed_destination = True
                if not markdown_original.same_object(marked):
                    _failure("analysis_publication_invalid")
                _win32.rollback_relative_handle(
                    markdown_handle,
                    parents.rendered,
                    markdown_basename,
                    original=markdown_original,
                )
                markdown_handle = None
            if matching_json:
                marked = _win32.set_delete_disposition(json_handle, delete=True)
                changed_destination = True
                if not json_original.same_object(marked):
                    _failure("analysis_publication_invalid")
                _win32.rollback_relative_handle(
                    json_handle,
                    parents.reports,
                    report_basename,
                    original=json_original,
                )
                json_handle = None

            if json_handle is not None:
                self._close_recovery_leaf(json_handle, parents)
                json_handle = None
            if markdown_handle is not None:
                self._close_recovery_leaf(markdown_handle, parents)
                markdown_handle = None

            fixed_code = (
                "report_invalid"
                if json_mismatch or markdown_mismatch
                else "publication_failed"
            )
            failed = self._failed_status(current, fixed_code=fixed_code)
            failed_result = self.cas_status(
                descriptor=bound,
                expected_status=current,
                status=failed,
            )
            if failed_result.applied:
                parents.close()
                parents = None
                self._finish_publication_release()
                return PublicationResult(failed_result.status, "failed")
            self._retain_publication_resources(
                failed_result,
                parents,
                json_original,
                markdown_original,
            )
            return PublicationResult(
                failed_result.status,
                "deferred",
                lease_retained=True,
            )
        except _AnalysisLeaseReleaseUncertain:
            self._publication_active = False
            raise
        except _win32.Win32QuarantineRequired as failure:
            self._retain_publication_resources(
                failure,
                parents,
                json_handle,
                markdown_handle,
                json_original,
                markdown_original,
            )
            return PublicationResult(current, "deferred", lease_retained=True)
        except BaseException as failure:
            if not isinstance(failure, Exception):
                self._retain_publication_resources(
                    failure,
                    parents,
                    json_handle,
                    markdown_handle,
                )
                raise
            # Once a matching destination was removed, any uncertainty must
            # retain the lease: it cannot safely claim a no-write retry.
            if changed_destination:
                self._retain_publication_resources(
                    failure,
                    parents,
                    json_handle,
                    markdown_handle,
                    json_original,
                    markdown_original,
                )
                return PublicationResult(current, "deferred", lease_retained=True)
            try:
                for handle in (json_handle, markdown_handle):
                    if handle is not None and not handle.closed:
                        self._close_recovery_leaf(handle, parents)
                json_handle = markdown_handle = None
                if parents is not None:
                    parents.close()
                    parents = None
            except BaseException as cleanup_failure:
                self._retain_publication_resources(
                    failure,
                    cleanup_failure,
                    parents,
                    json_handle,
                    markdown_handle,
                )
                return PublicationResult(current, "deferred", lease_retained=True)
            self._finish_publication_release()
            return PublicationResult(current, "deferred")


@contextmanager
def _short_lived_session(
    paths: AnalysisStatePaths,
) -> Iterator[AnalysisOutboxSession]:
    session = AnalysisOutboxSession.acquire(paths)
    try:
        yield session
    except Exception:
        if session.state == "active":
            session.release_normal()
        raise
    except BaseException:
        if session.state == "active":
            session.retain_for_quarantine()
        raise
    else:
        session.release_normal()


@contextmanager
def analysis_lease(paths: AnalysisStatePaths) -> Iterator[None]:
    """Compatibility scope for short, non-publication locked operations."""

    with _short_lived_session(paths):
        yield


def enqueue_analysis_source(
    *,
    paths: AnalysisStatePaths,
    source: ValidatedEvidenceSource,
    recipe: object,
) -> EnqueueResult:
    """Create or replay one descriptor and its pending status under lease."""

    try:
        stable_source = revalidate_validated_source(source)
        proposed = build_descriptor(
            source_kind=stable_source.source_kind,
            source_basis=stable_source.source_basis,
            recipe=recipe,
        )
    except (AnalysisContractError, EvidenceConsumerError) as exc:
        raise AnalysisOutboxError(
            "source_invalid",
            "analysis source is invalid",
        ) from exc
    with _short_lived_session(paths) as session:
        return session._enqueue_descriptor(proposed)


def replace_analysis_status(
    *,
    paths: AnalysisStatePaths,
    descriptor: object,
    expected_status: object,
    status: object,
) -> dict[str, Any]:
    """Compare-and-transition one status atomically under the analysis lease."""

    with _short_lived_session(paths) as session:
        result = session.cas_status(
            descriptor=descriptor,
            expected_status=expected_status,
            status=status,
        )
        if result.disposition == "ambiguous_not_applied":
            _failure(
                "analysis_status_interrupted",
                "analysis status update was interrupted",
            )
        return result.status


__all__ = (
    "AdapterTreeSlot",
    "AnalysisOutboxSession",
    "AnalysisOutboxError",
    "BoundAnalysisJob",
    "EnqueueResult",
    "NoAdapterControllerProof",
    "PublicationResult",
    "SelectedAnalysisJob",
    "StatusCasResult",
    "analysis_lease",
    "enqueue_analysis_source",
    "replace_analysis_status",
)
