"""Closed TG-M23 Windows process model and native capability preflight.

The current activation deliberately contains no native broker, provider, or
child-process launch.  Native execution therefore fails closed at a read-only,
pre-count capability boundary.  The fixed mock below drives the same ordered
process-safety state machine with a closed scenario set and a single bound
empty-claims fixture; callers cannot inject output bytes or event traces.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import NoReturn

from task_governance_tool import _analysis_win32 as _win32
from task_governance_tool._analysis_win32 import Win32BoundaryError


ATTEMPT_BUDGET_MS = 120_000
ALL_ATTEMPTS_BUDGET_MS = 240_000
PROOF_BUDGET_MS = 5_000
FINAL_BUDGET_MS = 1_000
STREAM_PREFIX_MAX_BYTES = 65_536
STDIN_MAX_BYTES = 262_144
PRIVATE_SCHEMA_MAX_BYTES = 65_536
_MOCK_WORKER_COUNT = 3
_MOCK_WORKER_JOIN_DURATION_MS = 17

BROKER_PRIVILEGES = frozenset(
    {
        "SeChangeNotifyPrivilege",
        "SeAssignPrimaryTokenPrivilege",
        "SeIncreaseQuotaPrivilege",
    }
)

_JOB_ID = re.compile(r"^tg_analysis_job_[0-9a-f]{16}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ATTEMPT_ROOT_BASENAME = re.compile(r"^\.taskgov-analysis-[a-z0-9]{8}$")
_ENVIRONMENT_KEYS = (
    "CODEX_HOME",
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "TEMP",
    "TMP",
)


@dataclass(frozen=True)
class ProcessSafetyError(RuntimeError):
    code: str = "analysis_process_unsafe"
    message: str = "analysis process boundary could not be proved safely"

    def __str__(self) -> str:
        return self.message


def _failure(
    code: str = "analysis_process_unsafe",
    message: str = "analysis process boundary could not be proved safely",
) -> NoReturn:
    raise ProcessSafetyError(code, message)


class ProcessQuarantineRequired(BaseException):
    """Non-returning native outcome represented as an inspectable mock fault."""

    def __init__(
        self,
        trace: tuple[str, ...],
        retained_objects: frozenset[str],
        *,
        root_capability: object | None = None,
    ) -> None:
        super().__init__("analysis process quarantine requires fail-fast termination")
        self.trace = trace
        self.retained_objects = retained_objects
        self.root_capability = root_capability


@dataclass(frozen=True)
class CapabilityPreflight:
    ready: bool
    inference_state: str
    adapter_attempt_count: int


def native_capability_preflight(
    privilege_reader: Callable[[], frozenset[str]] = _win32.current_token_privileges,
) -> CapabilityPreflight:
    """Return the current always-blocked native capability without mutation.

    Reading the caller token is the only observation.  Missing privileges are
    conclusive, while a privilege-capable caller remains blocked because this
    activation contains no approved immutable broker/runtime or live authority.
    """

    try:
        privileges = privilege_reader()
        if not isinstance(privileges, frozenset) or any(
            type(item) is not str or not item or "\0" in item for item in privileges
        ):
            raise TypeError
        # Evaluate the exact prerequisite without making it an activation switch.
        BROKER_PRIVILEGES.issubset(privileges)
    except (Exception, Win32BoundaryError):
        pass
    return CapabilityPreflight(
        ready=False,
        inference_state="policy_blocked",
        adapter_attempt_count=0,
    )


class NativeProcessBoundary:
    """Read-only current native boundary; child launch is intentionally absent."""

    def __init__(
        self,
        privilege_reader: Callable[
            [], frozenset[str]
        ] = _win32.current_token_privileges,
    ) -> None:
        self._privilege_reader = privilege_reader

    def preflight(self) -> CapabilityPreflight:
        return native_capability_preflight(self._privilege_reader)

    def execute_attempt(self, _attempt: object) -> NoReturn:
        preflight = self.preflight()
        if preflight.ready or preflight.adapter_attempt_count != 0:
            _failure()
        _failure("policy_blocked", "analysis process launch is policy blocked")


class MockScenario(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    CANCEL = "cancel"
    BROKER_CRASH = "broker_crash"
    WORKER_HANG = "worker_hang"
    PARTIAL_RESULT = "partial_result"
    WRONG_BINDING = "wrong_binding"
    POST_READ_CHANGED = "post_read_changed"
    TREE_ZERO_UNPROVED = "tree_zero_unproved"


class _CandidateFault(str, Enum):
    NONE = "none"
    WRONG_BINDING = "wrong_binding"
    POST_READ_CHANGED = "post_read_changed"


@dataclass(frozen=True)
class MockBinding:
    analysis_job_id: str
    source_key: str
    recipe_digest: str
    packet_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.analysis_job_id) is not str
            or _JOB_ID.fullmatch(self.analysis_job_id) is None
            or any(
                type(value) is not str or _DIGEST.fullmatch(value) is None
                for value in (
                    self.source_key,
                    self.recipe_digest,
                    self.packet_digest,
                )
            )
        ):
            _failure("analysis_mock_binding_invalid")


_ROOT_CAPABILITY_FACTORY_TOKEN = object()


class AttemptRootCapability:
    """Factory-sealed, session-owned binding to one retained attempt root.

    A physical capability borrows the held root and parent handles from the
    lease owner.  It owns only the private ``S``/``O`` handles created through
    that root.  A synthetic capability exists solely for cross-platform pure
    state-machine tests and can never be consumed as publication authority.
    """

    __slots__ = (
        "_analysis_job_id",
        "_attempt_number",
        "_output_handle",
        "_output_identity",
        "_owner_token",
        "_packet_digest",
        "_physical",
        "_root_basename",
        "_root_handle",
        "_root_identity",
        "_root_parent",
        "_schema_handle",
        "_schema_identity",
        "_state",
    )

    def __init__(
        self,
        token: object,
        *,
        analysis_job_id: str,
        attempt_number: int,
        packet_digest: str,
        owner_token: object,
        physical: bool,
        root_handle: object | None,
        root_parent: object | None,
        root_basename: str | None,
        root_identity: object | None,
    ) -> None:
        if token is not _ROOT_CAPABILITY_FACTORY_TOKEN:
            _failure("analysis_attempt_root_invalid")
        if (
            type(analysis_job_id) is not str
            or _JOB_ID.fullmatch(analysis_job_id) is None
            or type(attempt_number) is not int
            or attempt_number not in {1, 2}
            or type(packet_digest) is not str
            or _DIGEST.fullmatch(packet_digest) is None
            or owner_token is None
            or type(physical) is not bool
        ):
            _failure("analysis_attempt_root_invalid")
        if physical:
            if (
                not isinstance(root_handle, _win32.OwnedHandle)
                or not isinstance(root_parent, _win32.OwnedHandle)
                or type(root_basename) is not str
                or _ATTEMPT_ROOT_BASENAME.fullmatch(root_basename) is None
                or not isinstance(root_identity, _win32.HandleIdentity)
            ):
                _failure("analysis_attempt_root_invalid")
        elif any(
            item is not None
            for item in (root_handle, root_parent, root_basename, root_identity)
        ):
            _failure("analysis_attempt_root_invalid")
        self._analysis_job_id = analysis_job_id
        self._attempt_number = attempt_number
        self._output_handle = None
        self._output_identity = None
        self._owner_token = owner_token
        self._packet_digest = packet_digest
        self._physical = physical
        self._root_basename = root_basename
        self._root_handle = root_handle
        self._root_identity = root_identity
        self._root_parent = root_parent
        self._schema_handle = None
        self._schema_identity = None
        self._state = "bound"

    @property
    def analysis_job_id(self) -> str:
        return self._analysis_job_id

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def packet_digest(self) -> str:
        return self._packet_digest

    @property
    def is_physical(self) -> bool:
        return self._physical

    @property
    def state(self) -> str:
        return self._state

    def __repr__(self) -> str:
        return (
            "AttemptRootCapability("
            f"analysis_job_id={self._analysis_job_id!r}, "
            f"attempt_number={self._attempt_number!r}, "
            f"physical={self._physical!r}, state={self._state!r})"
        )

    def __copy__(self) -> NoReturn:
        _failure("analysis_attempt_root_invalid")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_attempt_root_invalid")

    def _matches(
        self,
        *,
        binding: MockBinding,
        attempt_number: int,
        owner_token: object | None = None,
    ) -> bool:
        return (
            isinstance(binding, MockBinding)
            and attempt_number == self._attempt_number
            and binding.analysis_job_id == self._analysis_job_id
            and binding.packet_digest == self._packet_digest
            and (owner_token is None or owner_token is self._owner_token)
        )

    def _prove_root(self, *, require_empty: bool = False) -> None:
        if not self._physical:
            return
        if self._state in {"removed", "quarantine"}:
            _failure("analysis_attempt_root_invalid")
        root = self._root_handle
        parent = self._root_parent
        original = self._root_identity
        if (
            not isinstance(root, _win32.OwnedHandle)
            or not isinstance(parent, _win32.OwnedHandle)
            or not isinstance(original, _win32.HandleIdentity)
            or root.closed
            or parent.closed
        ):
            _failure("analysis_attempt_root_invalid")
        observed = _win32.query_handle_identity(root)
        membership = _win32.prove_held_directory_membership(
            root,
            parent,
            self._root_basename,
        )
        if (
            not original.same_object(observed)
            or not original.same_object(membership)
            or not observed.is_directory
            or observed.is_reparse
            or observed.delete_pending
            or observed.link_count != 1
        ):
            raise _win32.Win32BoundaryError()
        if require_empty and _win32.enumerate_held_directory(
            root,
            maximum_entries=2,
        ):
            raise _win32.Win32BoundaryError()

    @staticmethod
    def _close_security(security: object | None) -> None:
        if isinstance(security, _win32.ExplicitSecurityDescriptor) and not security.closed:
            security.close()

    def _quarantine(self, trace: list[str], failure: BaseException) -> NoReturn:
        self._state = "quarantine"
        raise ProcessQuarantineRequired(
            tuple(trace),
            frozenset({f"n{self._attempt_number}:root"}),
            root_capability=self,
        ) from failure

    def _prepare_private_leaves(
        self,
        schema_bytes: bytes,
        *,
        trace: list[str],
    ) -> None:
        if self._state != "bound" or type(schema_bytes) is not bytes or not schema_bytes:
            _failure("analysis_attempt_root_invalid")
        if len(schema_bytes) > PRIVATE_SCHEMA_MAX_BYTES:
            _failure("analysis_attempt_root_invalid")
        if not self._physical:
            self._state = "prepared"
            return
        schema_security = output_security = None
        try:
            self._prove_root(require_empty=True)
            schema_security = _win32.ExplicitSecurityDescriptor.private_leaf(_win32.SP)
            self._schema_handle = _win32.create_relative_file(
                self._root_handle,
                "output-schema.json",
                _win32.SP,
                schema_security,
                kind="analysis-mock-output-schema",
            )
            self._schema_identity = _win32.write_flush_reread(
                self._schema_handle,
                schema_bytes,
                maximum=PRIVATE_SCHEMA_MAX_BYTES,
            )
            self._close_security(schema_security)
            schema_security = None

            output_security = _win32.ExplicitSecurityDescriptor.private_leaf(_win32.OP)
            self._output_handle = _win32.create_relative_file(
                self._root_handle,
                "output.json",
                _win32.OP,
                output_security,
                kind="analysis-mock-output",
            )
            self._output_identity = _win32.query_handle_identity(self._output_handle)
            self._close_security(output_security)
            output_security = None
            entries = _win32.enumerate_held_directory(
                self._root_handle,
                maximum_entries=2,
            )
            if [item.name for item in entries] != [
                "output-schema.json",
                "output.json",
            ]:
                raise _win32.Win32BoundaryError()
            self._prove_root()
            self._state = "prepared"
        except BaseException as failure:
            try:
                self._close_security(schema_security)
                self._close_security(output_security)
            except BaseException as close_failure:
                self._quarantine(trace, close_failure)
            if isinstance(failure, ProcessQuarantineRequired):
                raise
            self._quarantine(trace, failure)

    def _read_schema_as_target(self, expected: bytes, *, trace: list[str]) -> None:
        if not self._physical:
            return
        target = None
        try:
            self._prove_root()
            target = _win32.open_relative_file(
                self._root_handle,
                "output-schema.json",
                _win32.SC,
                kind="analysis-mock-target-schema",
            )
            target_identity = _win32.query_handle_identity(target)
            if (
                not self._schema_identity.same_object(target_identity)
                or _win32.read_handle_capped(
                    target,
                    maximum=PRIVATE_SCHEMA_MAX_BYTES,
                )
                != expected
            ):
                raise _win32.Win32BoundaryError()
            target.close()
            target = None
            owner_identity = _win32.prove_held_membership(
                self._schema_handle,
                self._root_handle,
                "output-schema.json",
            )
            if not self._schema_identity.same_object(owner_identity):
                raise _win32.Win32BoundaryError()
        except BaseException as failure:
            if target is not None and not target.closed:
                try:
                    target.close()
                except BaseException as close_failure:
                    self._quarantine(trace, close_failure)
            self._quarantine(trace, failure)

    def _write_output_as_target(self, document: bytes, *, trace: list[str]) -> None:
        if not self._physical:
            return
        target = None
        try:
            self._prove_root()
            target = _win32.open_relative_file(
                self._root_handle,
                "output.json",
                _win32.OC,
                owner_handle=self._output_handle,
                kind="analysis-mock-target-output",
            )
            _win32.write_handle_flush(
                target,
                document,
                maximum=STREAM_PREFIX_MAX_BYTES,
            )
            target.close()
            target = None
        except BaseException as failure:
            if target is not None and not target.closed:
                try:
                    target.close()
                except BaseException as close_failure:
                    self._quarantine(trace, close_failure)
            self._quarantine(trace, failure)

    def _output_snapshot(self, *, trace: list[str]) -> tuple[bytes, str]:
        if not self._physical:
            _failure("analysis_attempt_root_invalid")
        try:
            self._prove_root()
            membership = _win32.prove_held_membership(
                self._output_handle,
                self._root_handle,
                "output.json",
            )
            if not self._output_identity.same_object(membership):
                raise _win32.Win32BoundaryError()
            document = _win32.read_handle_capped(
                self._output_handle,
                maximum=STREAM_PREFIX_MAX_BYTES,
            )
            observed = _win32.query_handle_identity(self._output_handle)
            if not self._output_identity.same_object(observed):
                raise _win32.Win32BoundaryError()
            identity = (
                f"{observed.volume_serial_number}:"
                + observed.file_id.hex()
            )
            return document, identity
        except BaseException as failure:
            self._quarantine(trace, failure)

    def _finish_private_tree(self, *, trace: list[str], state: str) -> None:
        if state not in {"quiescent", "aborted"}:
            _failure("analysis_attempt_root_invalid")
        if self._state != "prepared":
            _failure("analysis_attempt_root_invalid")
        if not self._physical:
            self._state = state
            return
        try:
            self._prove_root()
            for handle in (self._schema_handle, self._output_handle):
                if not isinstance(handle, _win32.OwnedHandle) or handle.closed:
                    raise _win32.Win32BoundaryError()
                handle.close()
            self._schema_handle = None
            self._output_handle = None
            self._prove_root(require_empty=True)
            self._state = state
        except BaseException as failure:
            self._quarantine(trace, failure)

    def _remove_root(self, *, trace: list[str]) -> None:
        if self._state not in {"quiescent", "aborted"}:
            _failure("analysis_attempt_root_invalid")
        if not self._physical:
            self._state = "removed"
            return
        try:
            self._prove_root(require_empty=True)
            _win32.remove_relative_directory(
                self._root_handle,
                self._root_parent,
                self._root_basename,
            )
            self._state = "removed"
        except BaseException as failure:
            self._quarantine(trace, failure)

    def _consume_for_publication(self, *, trace: list[str]) -> None:
        if not self._physical or self._state != "quiescent":
            _failure("analysis_attempt_root_publication_invalid")
        try:
            self._prove_root(require_empty=True)
        except BaseException as failure:
            self._quarantine(trace, failure)
        self._state = "publication"

    def _remove_after_publication(self, *, trace: list[str]) -> None:
        if not self._physical or self._state != "publication_borrowed":
            _failure("analysis_attempt_root_publication_invalid")
        try:
            self._prove_root(require_empty=True)
            _win32.remove_relative_directory(
                self._root_handle,
                self._root_parent,
                self._root_basename,
            )
            self._state = "removed"
        except BaseException as failure:
            self._quarantine(trace, failure)


def create_physical_attempt_root_capability(
    *,
    root_parent: _win32.OwnedHandle,
    root_basename: str,
    analysis_job_id: str,
    attempt_number: int,
    packet_digest: str,
    owner_token: object,
) -> AttemptRootCapability:
    """Create-only and seal one exact fresh root under a held parent."""

    if (
        not isinstance(root_parent, _win32.OwnedHandle)
        or root_parent.closed
        or type(root_basename) is not str
        or _ATTEMPT_ROOT_BASENAME.fullmatch(root_basename) is None
        or type(analysis_job_id) is not str
        or _JOB_ID.fullmatch(analysis_job_id) is None
        or type(attempt_number) is not int
        or attempt_number not in {1, 2}
        or type(packet_digest) is not str
        or _DIGEST.fullmatch(packet_digest) is None
        or owner_token is None
    ):
        _failure("analysis_attempt_root_invalid")

    security = None
    root_handle = None
    capability = None
    retain_security = False
    try:
        security = _win32.ExplicitSecurityDescriptor.root()
        try:
            root_handle = _win32.create_relative_directory(
                root_parent,
                root_basename,
                security,
                kind="analysis-attempt-root",
            )
        except _win32.Win32QuarantineRequired:
            raise
        except Win32BoundaryError as failure:
            raise ProcessSafetyError(
                "analysis_attempt_root_create_failed",
                "analysis attempt root could not be created safely",
            ) from failure
        original = _win32.query_handle_identity(root_handle)
        capability = AttemptRootCapability(
            _ROOT_CAPABILITY_FACTORY_TOKEN,
            analysis_job_id=analysis_job_id,
            attempt_number=attempt_number,
            packet_digest=packet_digest,
            owner_token=owner_token,
            physical=True,
            root_handle=root_handle,
            root_parent=root_parent,
            root_basename=root_basename,
            root_identity=original,
        )
        membership = _win32.prove_held_directory_membership(
            root_handle,
            root_parent,
            root_basename,
        )
        proof = _win32.prove_exact_handle_security(root_handle, security)
        if (
            not original.same_object(membership)
            or not original.is_directory
            or original.is_reparse
            or original.delete_pending
            or original.link_count != 1
            or proof.policy != "root"
            or _win32.enumerate_held_directory(root_handle, maximum_entries=2)
        ):
            raise _win32.Win32BoundaryError()
        return capability
    except _win32.Win32QuarantineRequired:
        retain_security = True
        raise
    except (ProcessSafetyError, ProcessQuarantineRequired):
        raise
    except BaseException as failure:
        if capability is not None:
            capability._state = "quarantine"
            raise ProcessQuarantineRequired(
                (),
                frozenset({f"n{attempt_number}:root"}),
                root_capability=capability,
            ) from failure
        if root_handle is not None:
            retain_security = True
            raise _win32.Win32QuarantineRequired(
                "analysis_attempt_root_binding_unproved",
                handle=root_handle,
                resources=(root_parent, security),
            ) from failure
        raise ProcessSafetyError(
            "analysis_attempt_root_create_failed",
            "analysis attempt root could not be created safely",
        ) from failure
    finally:
        if security is not None and not security.closed and not retain_security:
            try:
                security.close()
            except _win32.Win32BoundaryError as failure:
                if capability is not None:
                    capability._state = "quarantine"
                    raise ProcessQuarantineRequired(
                        (),
                        frozenset({f"n{attempt_number}:root"}),
                        root_capability=capability,
                    ) from failure
                raise _win32.Win32QuarantineRequired(
                    "analysis_attempt_root_security_cleanup_unproved",
                    handle=root_handle,
                    resources=(root_parent, security),
                ) from failure


def _synthetic_attempt_root_capability_for_tests(
    *,
    binding: MockBinding,
    attempt_number: int,
    owner_token: object | None = None,
) -> AttemptRootCapability:
    """Return a non-publication capability for pure state-machine tests."""

    return AttemptRootCapability(
        _ROOT_CAPABILITY_FACTORY_TOKEN,
        analysis_job_id=binding.analysis_job_id,
        attempt_number=attempt_number,
        packet_digest=binding.packet_digest,
        owner_token=object() if owner_token is None else owner_token,
        physical=False,
        root_handle=None,
        root_parent=None,
        root_basename=None,
        root_identity=None,
    )


def prove_physical_attempt_root_capability(
    capability: AttemptRootCapability,
    *,
    owner_token: object,
    binding: MockBinding,
    attempt_number: int,
) -> None:
    """Reprove one exact session/root/binding before a later owner consumes it."""

    if (
        type(capability) is not AttemptRootCapability
        or not capability.is_physical
        or not capability._matches(
            binding=binding,
            attempt_number=attempt_number,
            owner_token=owner_token,
        )
    ):
        _failure("analysis_attempt_root_binding_invalid")
    try:
        capability._prove_root(require_empty=capability.state in {"quiescent", "publication"})
    except BaseException as failure:
        capability._quarantine([], failure)


@dataclass(frozen=True)
class AttemptInput:
    analysis_job_id: str
    attempt_number: int
    packet_digest: str
    stdin_bytes: bytes = field(repr=False)
    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = field(repr=False)
    cancel_requested: bool

    def __post_init__(self) -> None:
        if (
            type(self.analysis_job_id) is not str
            or _JOB_ID.fullmatch(self.analysis_job_id) is None
            or type(self.attempt_number) is not int
            or self.attempt_number not in {1, 2}
            or type(self.packet_digest) is not str
            or _DIGEST.fullmatch(self.packet_digest) is None
            or type(self.stdin_bytes) is not bytes
            or not self.stdin_bytes
            or len(self.stdin_bytes) > STDIN_MAX_BYTES
            or not isinstance(self.argv, tuple)
            or not self.argv
            or any(type(item) is not str or not item or "\0" in item for item in self.argv)
            or not isinstance(self.environment, tuple)
            or any(
                not isinstance(item, tuple)
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not str
                or not item[1]
                or "\0" in item[0]
                or "\0" in item[1]
                or item[0].startswith("=")
                for item in self.environment
            )
            or not isinstance(self.cancel_requested, bool)
        ):
            _failure("analysis_attempt_input_invalid")
        if tuple(item[0] for item in self.environment) != _ENVIRONMENT_KEYS:
            _failure("analysis_attempt_input_invalid")


@dataclass(frozen=True)
class SealedResult:
    analysis_job_id: str
    attempt_number: int
    packet_digest: str
    length: int
    digest: str
    document: bytes = field(repr=False)


@dataclass(frozen=True)
class _ResultCandidate:
    schema_version: int
    terminal_state: str
    analysis_job_id: str
    attempt_number: int
    packet_digest: str
    length: int
    digest: str
    document: bytes = field(repr=False)
    identity_before: str
    post_length: int
    post_digest: str
    post_document: bytes = field(repr=False)
    identity_after: str


_PROOF_FACTORY_TOKEN = object()


class AttemptTreeProof:
    """Bound, single-consumer proof for one retained quiescent attempt root."""

    __slots__ = (
        "_attempt_number",
        "_binding",
        "_consumed",
        "_root_capability",
        "_root_identity",
        "_trace",
    )

    def __init__(
        self,
        token: object,
        *,
        attempt_number: int,
        binding: MockBinding,
        root_capability: AttemptRootCapability,
        root_identity: str,
        trace: list[str],
    ) -> None:
        if token is not _PROOF_FACTORY_TOKEN:
            _failure("analysis_mock_tree_proof_invalid")
        self._attempt_number = attempt_number
        self._binding = binding
        self._root_capability = root_capability
        self._root_identity = root_identity
        self._trace = trace
        self._consumed = False

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    @property
    def absent_private_leaves(self) -> frozenset[str]:
        if self._root_capability.is_physical:
            return frozenset({"output-schema.json", "output.json"})
        prefix = f"n{self._attempt_number}:"
        return frozenset({prefix + "output_schema", prefix + "output"})

    @property
    def retained_objects(self) -> frozenset[str]:
        if self._root_capability.is_physical:
            return frozenset({"physical-attempt-root"})
        return frozenset({self._root_identity})

    @property
    def is_physical(self) -> bool:
        return self._root_capability.is_physical

    @property
    def trace(self) -> tuple[str, ...]:
        return tuple(self._trace)

    def __repr__(self) -> str:
        return (
            "AttemptTreeProof("
            f"attempt_number={self._attempt_number!r}, "
            f"binding={self._binding!r}, "
            f"root_identity={self._root_identity!r})"
        )

    def __copy__(self) -> NoReturn:
        _failure("analysis_mock_tree_proof_invalid")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_mock_tree_proof_invalid")

    def _discard(self, *, owner_token: object | None) -> "DiscardedAttemptTreeProof":
        if self._consumed:
            _failure("analysis_mock_tree_proof_consumed")
        capability = self._root_capability
        if capability.is_physical and owner_token is not capability._owner_token:
            _failure("analysis_attempt_root_binding_invalid")
        self._consumed = True
        if capability.is_physical:
            capability._remove_root(trace=self._trace)
        self._trace.append(f"n{self._attempt_number}:root_absent")
        return DiscardedAttemptTreeProof(
            _PROOF_FACTORY_TOKEN,
            attempt_number=self._attempt_number,
            binding=self._binding,
            root_capability=capability,
            root_identity=self._root_identity,
            trace=tuple(self._trace),
        )

    def _consume_for_publication(
        self,
        *,
        capability: AttemptRootCapability,
        owner_token: object,
        binding: MockBinding,
    ) -> "PublishReadyAttemptTreeProof":
        if (
            self._consumed
            or not self.is_physical
            or capability is not self._root_capability
            or not capability._matches(
                binding=binding,
                attempt_number=self._attempt_number,
                owner_token=owner_token,
            )
        ):
            _failure("analysis_attempt_root_publication_invalid")
        self._consumed = True
        capability._consume_for_publication(trace=self._trace)
        return PublishReadyAttemptTreeProof(
            _PROOF_FACTORY_TOKEN,
            attempt_number=self._attempt_number,
            binding=self._binding,
            root_capability=capability,
            trace=tuple(self._trace),
        )


class DiscardedAttemptTreeProof:
    """Single-use physical prerequisite for preparing a fresh retry root."""

    __slots__ = (
        "_attempt_number",
        "_binding",
        "_retry_consumed",
        "_root_capability",
        "_root_identity",
        "_trace",
    )

    def __init__(
        self,
        token: object,
        *,
        attempt_number: int,
        binding: MockBinding,
        root_capability: AttemptRootCapability,
        root_identity: str,
        trace: tuple[str, ...],
    ) -> None:
        if token is not _PROOF_FACTORY_TOKEN:
            _failure("analysis_mock_tree_proof_invalid")
        self._attempt_number = attempt_number
        self._binding = binding
        self._root_capability = root_capability
        self._root_identity = root_identity
        self._trace = trace
        self._retry_consumed = False

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    @property
    def absent_objects(self) -> frozenset[str]:
        if self._root_capability.is_physical:
            return frozenset(
                {"physical-attempt-root", "output-schema.json", "output.json"}
            )
        prefix = f"n{self._attempt_number}:"
        return frozenset(
            {
                self._root_identity,
                prefix + "output_schema",
                prefix + "output",
            }
        )

    @property
    def trace(self) -> tuple[str, ...]:
        return self._trace

    def __repr__(self) -> str:
        return (
            "DiscardedAttemptTreeProof("
            f"attempt_number={self._attempt_number!r}, "
            f"binding={self._binding!r}, "
            f"root_identity={self._root_identity!r})"
        )

    def __copy__(self) -> NoReturn:
        _failure("analysis_mock_tree_proof_invalid")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_mock_tree_proof_invalid")

    def _consume_for_retry(
        self,
        binding: MockBinding,
        root_capability: AttemptRootCapability,
    ) -> None:
        if (
            self._retry_consumed
            or self._attempt_number != 1
            or self._binding != binding
            or type(root_capability) is not AttemptRootCapability
            or root_capability is self._root_capability
            or root_capability.attempt_number != 2
            or root_capability.analysis_job_id != binding.analysis_job_id
            or root_capability.packet_digest != binding.packet_digest
            or root_capability.is_physical != self._root_capability.is_physical
            or (
                root_capability.is_physical
                and (
                    self._root_capability.state != "removed"
                    or root_capability._owner_token
                    is not self._root_capability._owner_token
                    or root_capability._root_basename
                    == self._root_capability._root_basename
                    or root_capability._root_handle
                    is self._root_capability._root_handle
                    or self._root_capability._root_identity.same_object(
                        root_capability._root_identity
                    )
                )
            )
        ):
            _failure("analysis_mock_retry_proof_invalid")
        self._retry_consumed = True


class PublishReadyAttemptTreeProof:
    """Single-use physical tree proof consumed for same-root publication."""

    __slots__ = (
        "_attempt_number",
        "_binding",
        "_borrow_consumed",
        "_root_capability",
        "_trace",
    )

    def __init__(
        self,
        token: object,
        *,
        attempt_number: int,
        binding: MockBinding,
        root_capability: AttemptRootCapability,
        trace: tuple[str, ...],
    ) -> None:
        if (
            token is not _PROOF_FACTORY_TOKEN
            or not root_capability.is_physical
            or root_capability.state != "publication"
        ):
            _failure("analysis_attempt_root_publication_invalid")
        self._attempt_number = attempt_number
        self._binding = binding
        self._borrow_consumed = False
        self._root_capability = root_capability
        self._trace = trace

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    @property
    def trace(self) -> tuple[str, ...]:
        return self._trace

    def __repr__(self) -> str:
        return (
            "PublishReadyAttemptTreeProof("
            f"attempt_number={self._attempt_number!r}, binding={self._binding!r})"
        )

    def __copy__(self) -> NoReturn:
        _failure("analysis_attempt_root_publication_invalid")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_attempt_root_publication_invalid")

    def _borrow(
        self,
        *,
        owner_token: object,
        binding: MockBinding,
        attempt_number: int,
    ) -> "BorrowedPublicationRoot":
        capability = self._root_capability
        if (
            self._borrow_consumed
            or attempt_number != self._attempt_number
            or binding != self._binding
            or not capability._matches(
                binding=binding,
                attempt_number=attempt_number,
                owner_token=owner_token,
            )
            or capability.state != "publication"
        ):
            _failure("analysis_attempt_root_publication_invalid")
        try:
            capability._prove_root(require_empty=True)
        except BaseException as failure:
            capability._quarantine(list(self._trace), failure)
        borrowed = BorrowedPublicationRoot(
            _PROOF_FACTORY_TOKEN,
            attempt_number=attempt_number,
            binding=binding,
            root_capability=capability,
            trace=self._trace,
        )
        self._borrow_consumed = True
        capability._state = "publication_borrowed"
        return borrowed


class BorrowedPublicationRoot:
    """One-shot exact held-root view for report creation and final removal."""

    __slots__ = (
        "_attempt_number",
        "_binding",
        "_completion_consumed",
        "_root_basename",
        "_root_capability",
        "_root_handle",
        "_root_parent",
        "_trace",
    )

    def __init__(
        self,
        token: object,
        *,
        attempt_number: int,
        binding: MockBinding,
        root_capability: AttemptRootCapability,
        trace: tuple[str, ...],
    ) -> None:
        if (
            token is not _PROOF_FACTORY_TOKEN
            or not root_capability.is_physical
            or root_capability.state != "publication"
        ):
            _failure("analysis_attempt_root_publication_invalid")
        self._attempt_number = attempt_number
        self._binding = binding
        self._completion_consumed = False
        self._root_basename = root_capability._root_basename
        self._root_capability = root_capability
        self._root_handle = root_capability._root_handle
        self._root_parent = root_capability._root_parent
        self._trace = trace

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    @property
    def root_handle(self) -> _win32.OwnedHandle:
        return self._root_handle

    @property
    def root_parent(self) -> _win32.OwnedHandle:
        return self._root_parent

    @property
    def root_basename(self) -> str:
        return self._root_basename

    @property
    def root_capability(self) -> AttemptRootCapability:
        return self._root_capability

    def __repr__(self) -> str:
        return (
            "BorrowedPublicationRoot("
            f"attempt_number={self._attempt_number!r}, "
            f"binding={self._binding!r})"
        )

    def __copy__(self) -> NoReturn:
        _failure("analysis_attempt_root_publication_invalid")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_attempt_root_publication_invalid")

    def _remove(
        self,
        *,
        owner_token: object,
        binding: MockBinding,
        attempt_number: int,
    ) -> None:
        capability = self._root_capability
        if (
            self._completion_consumed
            or attempt_number != self._attempt_number
            or binding != self._binding
            or self._root_handle is not capability._root_handle
            or self._root_parent is not capability._root_parent
            or self._root_basename != capability._root_basename
            or not capability._matches(
                binding=binding,
                attempt_number=attempt_number,
                owner_token=owner_token,
            )
            or capability.state != "publication_borrowed"
        ):
            _failure("analysis_attempt_root_publication_invalid")
        self._completion_consumed = True
        capability._remove_after_publication(trace=list(self._trace))


class AbortedAttemptTreeProof:
    """Typed pre-launch abort proof; the retained empty root is owner-cleaned."""

    __slots__ = (
        "_attempt_number",
        "_binding",
        "_cleanup_consumed",
        "_root_capability",
        "_trace",
    )

    def __init__(
        self,
        token: object,
        *,
        attempt_number: int,
        binding: MockBinding,
        root_capability: AttemptRootCapability,
        trace: tuple[str, ...],
    ) -> None:
        if token is not _PROOF_FACTORY_TOKEN or root_capability.state != "aborted":
            _failure("analysis_mock_abort_invalid")
        self._attempt_number = attempt_number
        self._binding = binding
        self._cleanup_consumed = False
        self._root_capability = root_capability
        self._trace = trace

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    @property
    def is_physical(self) -> bool:
        return self._root_capability.is_physical

    @property
    def trace(self) -> tuple[str, ...]:
        return self._trace

    def __repr__(self) -> str:
        return (
            "AbortedAttemptTreeProof("
            f"attempt_number={self._attempt_number!r}, binding={self._binding!r})"
        )

    def __copy__(self) -> NoReturn:
        _failure("analysis_mock_abort_invalid")

    def __deepcopy__(self, _memo: object) -> NoReturn:
        _failure("analysis_mock_abort_invalid")


@dataclass(frozen=True)
class AttemptOutcome:
    inference_state: str
    duration_ms: int
    sealed_result: SealedResult | None
    tree_proof: AttemptTreeProof
    worker_join_duration_ms: int | None


@dataclass(frozen=True)
class OwnershipSnapshot:
    phase: str
    controller: frozenset[str]
    broker: frozenset[str]
    target: frozenset[str]
    workers: tuple[tuple[str, frozenset[str]], ...]


@dataclass(frozen=True)
class MockAttemptResult:
    scenario: MockScenario
    attempt_number: int
    outcome: AttemptOutcome
    trace: tuple[str, ...]
    attempt_identities: frozenset[str]
    ownership: OwnershipSnapshot


def _fixture_document(binding: MockBinding) -> bytes:
    # Exact canonical ordering, with no caller-controlled claim content.
    return (
        b'{"analysis_job_id":"'
        + binding.analysis_job_id.encode("ascii")
        + b'","claims":[],"output_schema_version":1,"recipe_digest":"'
        + binding.recipe_digest.encode("ascii")
        + b'","source_key":"'
        + binding.source_key.encode("ascii")
        + b'"}'
    )


def _validate_result_candidate(
    candidate: _ResultCandidate,
    *,
    attempt: AttemptInput,
    binding: MockBinding,
) -> SealedResult | None:
    """Validate untrusted Q metadata and bytes before exposing a result."""

    if (
        not isinstance(candidate, _ResultCandidate)
        or type(candidate.document) is not bytes
        or type(candidate.post_document) is not bytes
    ):
        return None
    expected_document = _fixture_document(binding)
    expected_digest = "sha256:" + hashlib.sha256(candidate.document).hexdigest()
    expected_post_digest = (
        "sha256:" + hashlib.sha256(candidate.post_document).hexdigest()
    )
    if (
        binding.analysis_job_id != attempt.analysis_job_id
        or binding.packet_digest != attempt.packet_digest
        or type(candidate.schema_version) is not int
        or candidate.schema_version != 1
        or type(candidate.terminal_state) is not str
        or candidate.terminal_state != "succeeded"
        or type(candidate.analysis_job_id) is not str
        or candidate.analysis_job_id != attempt.analysis_job_id
        or type(candidate.attempt_number) is not int
        or candidate.attempt_number != attempt.attempt_number
        or type(candidate.packet_digest) is not str
        or candidate.packet_digest != attempt.packet_digest
        or type(candidate.length) is not int
        or candidate.length < 0
        or candidate.length > STREAM_PREFIX_MAX_BYTES
        or candidate.length != len(candidate.document)
        or type(candidate.digest) is not str
        or candidate.digest != expected_digest
        or candidate.document != expected_document
        or type(candidate.post_length) is not int
        or candidate.post_length != len(candidate.post_document)
        or candidate.post_length != candidate.length
        or type(candidate.post_digest) is not str
        or candidate.post_digest != expected_post_digest
        or candidate.post_digest != candidate.digest
        or candidate.post_document != candidate.document
        or type(candidate.identity_before) is not str
        or not candidate.identity_before
        or "\0" in candidate.identity_before
        or type(candidate.identity_after) is not str
        or "\0" in candidate.identity_after
        or candidate.identity_before != candidate.identity_after
    ):
        return None
    return SealedResult(
        analysis_job_id=candidate.analysis_job_id,
        attempt_number=candidate.attempt_number,
        packet_digest=candidate.packet_digest,
        length=candidate.length,
        digest=candidate.digest,
        document=candidate.document,
    )


def _candidate_for_attempt(
    *,
    attempt: AttemptInput,
    binding: MockBinding,
    fault: _CandidateFault,
) -> _ResultCandidate:
    if not isinstance(fault, _CandidateFault):
        _failure("analysis_mock_state_invalid")
    document = _fixture_document(binding)
    identity_before = f"n{attempt.attempt_number}:q-file-id"
    analysis_job_id = attempt.analysis_job_id
    identity_after = identity_before
    post_document = document
    if fault == _CandidateFault.WRONG_BINDING:
        replacement = "0" if analysis_job_id[-1] != "0" else "1"
        analysis_job_id = analysis_job_id[:-1] + replacement
    elif fault == _CandidateFault.POST_READ_CHANGED:
        post_document = b"X" + document[1:]
    return _ResultCandidate(
        schema_version=1,
        terminal_state="succeeded",
        analysis_job_id=analysis_job_id,
        attempt_number=attempt.attempt_number,
        packet_digest=attempt.packet_digest,
        length=len(document),
        digest="sha256:" + hashlib.sha256(document).hexdigest(),
        document=document,
        identity_before=identity_before,
        post_length=len(post_document),
        post_digest="sha256:" + hashlib.sha256(post_document).hexdigest(),
        post_document=post_document,
        identity_after=identity_after,
    )


def _attempt_identities(number: int) -> frozenset[str]:
    prefix = f"n{number}:"
    return frozenset(
        prefix + name
        for name in (
            "root",
            "job",
            "broker_process",
            "broker_primary_thread",
            "broker_token_creation",
            "target_token_copy",
            "input_mapping",
            "result_mapping",
            "broker_event",
            "controller_event",
            "input_duplicate",
            "result_duplicate",
            "broker_event_duplicate",
            "controller_event_duplicate",
            "target_token_duplicate",
            "station",
            "desktop",
            "target_process",
            "target_thread",
            "stdin:read",
            "stdout:write",
            "stderr:write",
            "stdin_pipe:write",
            "stdout_pipe:read",
            "stderr_pipe:read",
            "stdin_worker_handle",
            "stdout_worker_handle",
            "stderr_worker_handle",
            "stdin_worker",
            "stdout_worker",
            "stderr_worker",
            "output_schema",
            "output",
            "result_mapping:write",
        )
    )


def _ownership(number: int) -> OwnershipSnapshot:
    prefix = f"n{number}:"
    controller = frozenset(
        {
            prefix + "job",
            prefix + "broker_process",
            prefix + "input_mapping",
            prefix + "result_mapping",
            prefix + "broker_event",
            prefix + "controller_event",
        }
    )
    broker = frozenset(
        {
            prefix + "root",
            prefix + "station",
            prefix + "desktop",
            prefix + "target_process",
            prefix + "target_thread",
            prefix + "stdin_worker_handle",
            prefix + "stdout_worker_handle",
            prefix + "stderr_worker_handle",
            prefix + "output_schema",
            prefix + "output",
            prefix + "result_mapping:write",
        }
    )
    target = frozenset(
        {
            prefix + "stdin:read",
            prefix + "stdout:write",
            prefix + "stderr:write",
        }
    )
    workers = (
        (
            "stdin",
            frozenset({prefix + "stdin_pipe:write", prefix + "stdin_worker"}),
        ),
        (
            "stdout",
            frozenset({prefix + "stdout_pipe:read", prefix + "stdout_worker"}),
        ),
        (
            "stderr",
            frozenset({prefix + "stderr_pipe:read", prefix + "stderr_worker"}),
        ),
    )
    owned_sets = (controller, broker, target, *(items for _, items in workers))
    for index, owned in enumerate(owned_sets):
        for other in owned_sets[index + 1 :]:
            if owned & other:
                _failure("analysis_mock_ownership_invalid")
    return OwnershipSnapshot(
        phase="post_target_launch",
        controller=controller,
        broker=broker,
        target=target,
        workers=workers,
    )


class _AttemptMachine:
    def __init__(
        self,
        attempt: AttemptInput,
        binding: MockBinding,
        trace: list[str],
        root_capability: AttemptRootCapability,
        output_schema_bytes: bytes,
    ) -> None:
        if (
            attempt.analysis_job_id != binding.analysis_job_id
            or attempt.packet_digest != binding.packet_digest
            or type(root_capability) is not AttemptRootCapability
            or not root_capability._matches(
                binding=binding,
                attempt_number=attempt.attempt_number,
            )
            or type(output_schema_bytes) is not bytes
            or not output_schema_bytes
        ):
            _failure("analysis_mock_binding_invalid")
        self.attempt: AttemptInput | None = attempt
        self.binding = binding
        self.trace = trace
        self.root_capability = root_capability
        self.output_schema_bytes = output_schema_bytes
        self.phase = "new"
        self.identities = _attempt_identities(attempt.attempt_number)
        self.ownership = _ownership(attempt.attempt_number)

    @property
    def prefix(self) -> str:
        attempt = self.attempt
        if attempt is None:
            _failure("analysis_mock_state_invalid")
        return f"n{attempt.attempt_number}:"

    def release_attempt_input(self) -> None:
        """Release the only machine-owned stdin/environment container."""

        self.attempt = None

    def _event(self, event: str) -> None:
        self.trace.append(self.prefix + event)

    def _require(self, phase: str) -> None:
        if self.phase != phase:
            _failure("analysis_mock_state_invalid")

    def prepare(self) -> None:
        self._require("new")
        self.root_capability._prepare_private_leaves(
            self.output_schema_bytes,
            trace=self.trace,
        )
        self._event("fresh_objects")
        self._event("ownership_proved")
        self._event("schema_flushed")
        self.phase = "prepared"

    def abort(self) -> AbortedAttemptTreeProof:
        self._require("prepared")
        self._event("prepared_abort_latched")
        self.root_capability._finish_private_tree(
            trace=self.trace,
            state="aborted",
        )
        self._event("s_o_absent")
        self._event("prepared_abort_complete")
        self.phase = "aborted"
        return AbortedAttemptTreeProof(
            _PROOF_FACTORY_TOKEN,
            attempt_number=self.attempt.attempt_number,
            binding=self.binding,
            root_capability=self.root_capability,
            trace=tuple(self.trace),
        )

    def mark_recorded(self) -> None:
        self._require("prepared")
        self._event("attempt_recorded")
        self.phase = "recorded"

    def launch(self) -> None:
        self._require("recorded")
        self.root_capability._read_schema_as_target(
            self.output_schema_bytes,
            trace=self.trace,
        )
        for event in (
            "broker_launch_proved",
            "broker_resumed_once",
            "broker_primary_thread_closed",
            "parent_duplicates_closed",
            "broker_token_proved",
            "private_desktop_proved",
            "target_token_transferred_to_broker",
            "controller_target_token_copy_closed",
            "target_launch_proved",
            "broker_target_creation_token_closed",
            "target_resumed_once",
        ):
            self._event(event)
        self.phase = "launched"

    def target_signaled(self) -> None:
        self._require("launched")
        self._event("target_signaled")
        self.phase = "target_signaled"

    def join_workers(self) -> int:
        self._require("target_signaled")
        for event in (
            "stdin_source_closed",
            "stdin_unused_pipe_end_closed",
            "stdin_joined",
            "stdout_unused_pipe_end_closed",
            "stdout_eof",
            "stdout_joined",
            "stderr_unused_pipe_end_closed",
            "stderr_eof",
            "stderr_joined",
            f"worker_count_{_MOCK_WORKER_COUNT}",
            "worker_join_duration_bounded",
        ):
            self._event(event)
        self.phase = "workers_joined"
        return _MOCK_WORKER_JOIN_DURATION_MS

    def _retained_tree_proof(self) -> AttemptTreeProof:
        if self.root_capability.state != "quiescent":
            _failure("analysis_mock_tree_proof_invalid")
        self._event("root_retained")
        self._event("tree_quiescent")
        self.phase = "finished"
        return AttemptTreeProof(
            _PROOF_FACTORY_TOKEN,
            attempt_number=self.attempt.attempt_number,
            binding=self.binding,
            root_capability=self.root_capability,
            root_identity=self.prefix + "root",
            trace=self.trace,
        )

    def success(self, *, candidate_fault: _CandidateFault) -> AttemptOutcome:
        self._require("workers_joined")
        candidate = _candidate_for_attempt(
            attempt=self.attempt,
            binding=self.binding,
            fault=candidate_fault,
        )
        if self.root_capability.is_physical:
            self.root_capability._write_output_as_target(
                candidate.document,
                trace=self.trace,
            )
            document, identity_before = self.root_capability._output_snapshot(
                trace=self.trace,
            )
            if candidate_fault == _CandidateFault.POST_READ_CHANGED:
                self.root_capability._write_output_as_target(
                    candidate.post_document,
                    trace=self.trace,
                )
            post_document, identity_after = self.root_capability._output_snapshot(
                trace=self.trace,
            )
            candidate = _ResultCandidate(
                schema_version=candidate.schema_version,
                terminal_state=candidate.terminal_state,
                analysis_job_id=candidate.analysis_job_id,
                attempt_number=candidate.attempt_number,
                packet_digest=candidate.packet_digest,
                length=len(document),
                digest="sha256:" + hashlib.sha256(document).hexdigest(),
                document=document,
                identity_before=identity_before,
                post_length=len(post_document),
                post_digest=(
                    "sha256:" + hashlib.sha256(post_document).hexdigest()
                ),
                post_document=post_document,
                identity_after=identity_after,
            )
        for event in (
            "target_handles_closed",
            "job_b_only_1",
            "job_b_only_2",
            "output_held_before",
            "output_read_capped",
            "output_held_after",
            "q_sealed",
        ):
            self._event(event)
        self.root_capability._finish_private_tree(
            trace=self.trace,
            state="quiescent",
        )
        self._event("s_o_absent")
        for event in (
            "broker_terminal",
            "broker_exited",
            "job_zero_1",
            "job_zero_2",
            "q_reread",
        ):
            self._event(event)
        result = _validate_result_candidate(
            candidate,
            attempt=self.attempt,
            binding=self.binding,
        )
        if result is not None:
            state = "succeeded"
            self._event("binding_valid")
        else:
            state = "invalid_output"
            self._event("binding_invalid")
        for event in ("attempt_handles_closed", "job_closed"):
            self._event(event)
        tree_proof = self._retained_tree_proof()
        return AttemptOutcome(
            state,
            25,
            result,
            tree_proof,
            _MOCK_WORKER_JOIN_DURATION_MS,
        )

    def abnormal(
        self,
        *,
        reason: str,
        inference_state: str,
        duration_ms: int,
        zero_proved: bool,
    ) -> AttemptOutcome:
        if self.phase not in {"launched", "target_signaled"}:
            _failure("analysis_mock_state_invalid")
        self._event("outcome_latched:" + reason)
        self._event("terminate_job")
        self._event("broker_signaled")
        self._event("job_zero_1")
        if not zero_proved:
            self._event("job_zero_unproved")
            self._event("quarantine_fail_fast")
            self.phase = "quarantine"
            self.root_capability._state = "quarantine"
            raise ProcessQuarantineRequired(
                tuple(self.trace),
                self.identities,
                root_capability=self.root_capability,
            )
        for event in (
            "job_zero_2",
            "q_unread",
        ):
            self._event(event)
        self.root_capability._finish_private_tree(
            trace=self.trace,
            state="quiescent",
        )
        self._event("s_o_absent")
        for event in ("attempt_handles_closed", "job_closed"):
            self._event(event)
        tree_proof = self._retained_tree_proof()
        return AttemptOutcome(
            inference_state,
            duration_ms,
            None,
            tree_proof,
            None,
        )


def _attempt(
    *,
    binding: MockBinding,
    number: int,
    stdin_bytes: bytes,
    argv: tuple[str, ...],
    environment: tuple[tuple[str, str], ...],
    cancel_requested: bool,
) -> AttemptInput:
    return AttemptInput(
        analysis_job_id=binding.analysis_job_id,
        attempt_number=number,
        packet_digest=binding.packet_digest,
        stdin_bytes=stdin_bytes,
        argv=argv,
        environment=environment,
        cancel_requested=cancel_requested,
    )


def _run_one(
    scenario: MockScenario,
    machine: _AttemptMachine,
) -> AttemptOutcome:
    machine.launch()
    if scenario == MockScenario.TIMEOUT:
        return machine.abnormal(
            reason="timeout",
            inference_state="timeout",
            duration_ms=ATTEMPT_BUDGET_MS,
            zero_proved=True,
        )
    if scenario == MockScenario.CANCEL:
        if not machine.attempt.cancel_requested:
            _failure("analysis_mock_state_invalid")
        return machine.abnormal(
            reason="cancel",
            inference_state="cancelled",
            duration_ms=10,
            zero_proved=True,
        )
    if scenario == MockScenario.BROKER_CRASH:
        return machine.abnormal(
            reason="broker_crash",
            inference_state="failed",
            duration_ms=20,
            zero_proved=True,
        )
    if scenario == MockScenario.PARTIAL_RESULT:
        machine._event("partial_q_latched")
        return machine.abnormal(
            reason="partial_q",
            inference_state="failed",
            duration_ms=20,
            zero_proved=True,
        )
    if scenario == MockScenario.TREE_ZERO_UNPROVED:
        return machine.abnormal(
            reason="timeout",
            inference_state="timeout",
            duration_ms=ATTEMPT_BUDGET_MS,
            zero_proved=False,
        )

    machine.target_signaled()
    if scenario == MockScenario.WORKER_HANG:
        machine._event("worker_join_timeout")
        return machine.abnormal(
            reason="worker_hang",
            inference_state="failed",
            duration_ms=PROOF_BUDGET_MS,
            zero_proved=True,
        )
    machine.join_workers()
    candidate_fault = _CandidateFault.NONE
    if scenario == MockScenario.WRONG_BINDING:
        candidate_fault = _CandidateFault.WRONG_BINDING
    elif scenario == MockScenario.POST_READ_CHANGED:
        candidate_fault = _CandidateFault.POST_READ_CHANGED
    return machine.success(candidate_fault=candidate_fault)


class PreparedMockAttempt:
    """Opaque fresh attempt waiting for the caller-owned status CAS marker."""

    __slots__ = (
        "_attempt_number",
        "_binding",
        "_final_trace",
        "_machine",
        "_scenario",
        "_state",
    )

    def __init__(
        self,
        token: object,
        *,
        machine: _AttemptMachine,
        scenario: MockScenario,
    ) -> None:
        if token is not _PROOF_FACTORY_TOKEN:
            _failure("analysis_mock_preparation_invalid")
        attempt = machine.attempt
        if attempt is None:
            _failure("analysis_mock_preparation_invalid")
        self._attempt_number = attempt.attempt_number
        self._binding = machine.binding
        self._final_trace: tuple[str, ...] = ()
        self._machine = machine
        self._scenario = scenario
        self._state = "prepared"

    @property
    def attempt_number(self) -> int:
        return self._attempt_number

    @property
    def binding(self) -> MockBinding:
        return self._binding

    @property
    def trace(self) -> tuple[str, ...]:
        if self._machine is not None:
            return tuple(self._machine.trace)
        return self._final_trace

    def __repr__(self) -> str:
        return (
            "PreparedMockAttempt("
            f"attempt_number={self.attempt_number!r}, "
            f"binding={self.binding!r}, state={self._state!r})"
        )

    def _mark_recorded(self) -> None:
        if self._state != "prepared":
            _failure("analysis_mock_record_marker_invalid")
        machine = self._machine
        if machine is None:
            _failure("analysis_mock_record_marker_invalid")
        machine.mark_recorded()
        self._state = "recorded"

    def _abort(self) -> AbortedAttemptTreeProof:
        if self._state != "prepared":
            _failure("analysis_mock_abort_invalid")
        machine = self._machine
        if machine is None:
            _failure("analysis_mock_abort_invalid")
        self._state = "aborted"
        quarantine = False
        try:
            proof = machine.abort()
            return proof
        except ProcessQuarantineRequired:
            quarantine = True
            self._state = "quarantine"
            raise
        finally:
            if not quarantine:
                self._final_trace = tuple(machine.trace)
                machine.release_attempt_input()
                self._machine = None
                self._scenario = None

    def _execute(self) -> MockAttemptResult:
        if self._state != "recorded":
            _failure("analysis_mock_execute_invalid")
        # Consume before any launch transition so a raised quarantine fault or
        # any other failure can never make this capability executable again.
        machine = self._machine
        scenario = self._scenario
        if machine is None or scenario is None:
            _failure("analysis_mock_execute_invalid")
        self._state = "executed"
        quarantine = False
        try:
            outcome = _run_one(scenario, machine)
            return MockAttemptResult(
                scenario=scenario,
                attempt_number=self._attempt_number,
                outcome=outcome,
                trace=tuple(machine.trace),
                attempt_identities=machine.identities,
                ownership=machine.ownership,
            )
        except ProcessQuarantineRequired:
            quarantine = True
            raise
        finally:
            if not quarantine:
                self._final_trace = tuple(machine.trace)
                machine.release_attempt_input()
                self._machine = None
                self._scenario = None


def prepare_closed_mock_attempt(
    attempt_number: int,
    scenario: MockScenario,
    *,
    binding: MockBinding,
    stdin_bytes: bytes,
    argv: tuple[str, ...],
    environment: tuple[tuple[str, str], ...],
    root_capability: AttemptRootCapability,
    output_schema_bytes: bytes,
    prior_discard: DiscardedAttemptTreeProof | None = None,
) -> PreparedMockAttempt:
    """Create and flush one fresh attempt without recording or launching it."""

    if (
        type(attempt_number) is not int
        or attempt_number not in {1, 2}
        or not isinstance(scenario, MockScenario)
        or not isinstance(binding, MockBinding)
        or type(root_capability) is not AttemptRootCapability
        or root_capability.state != "bound"
        or not root_capability._matches(
            binding=binding,
            attempt_number=attempt_number,
        )
        or type(output_schema_bytes) is not bytes
        or not output_schema_bytes
        or len(output_schema_bytes) > PRIVATE_SCHEMA_MAX_BYTES
    ):
        _failure("analysis_mock_scenario_invalid")
    attempt = _attempt(
        binding=binding,
        number=attempt_number,
        stdin_bytes=stdin_bytes,
        argv=argv,
        environment=environment,
        cancel_requested=scenario == MockScenario.CANCEL,
    )
    if attempt_number == 1:
        if prior_discard is not None:
            _failure("analysis_mock_retry_proof_invalid")
    else:
        if type(prior_discard) is not DiscardedAttemptTreeProof:
            _failure("analysis_mock_retry_proof_invalid")
        prior_discard._consume_for_retry(binding, root_capability)
    trace: list[str] = []
    machine = _AttemptMachine(
        attempt,
        binding,
        trace,
        root_capability,
        output_schema_bytes,
    )
    machine.prepare()
    return PreparedMockAttempt(
        _PROOF_FACTORY_TOKEN,
        machine=machine,
        scenario=scenario,
    )


def mark_prepared_mock_attempt_recorded(prepared: PreparedMockAttempt) -> None:
    """Mark the caller-owned status CAS exactly once; this writes no status."""

    if type(prepared) is not PreparedMockAttempt:
        _failure("analysis_mock_preparation_invalid")
    prepared._mark_recorded()


def abort_prepared_mock_attempt(
    prepared: PreparedMockAttempt,
) -> AbortedAttemptTreeProof:
    """Abort one pre-marker preparation without any launch transition."""

    if type(prepared) is not PreparedMockAttempt:
        _failure("analysis_mock_preparation_invalid")
    return prepared._abort()


def execute_prepared_mock_attempt(
    prepared: PreparedMockAttempt,
) -> MockAttemptResult:
    """Execute one previously recorded preparation exactly once."""

    if type(prepared) is not PreparedMockAttempt:
        _failure("analysis_mock_preparation_invalid")
    return prepared._execute()


def discard_attempt_tree(
    proof: AttemptTreeProof,
    *,
    root_owner_token: object | None = None,
) -> DiscardedAttemptTreeProof:
    """Consume a retained tree proof and prove that exact root absent."""

    if type(proof) is not AttemptTreeProof:
        _failure("analysis_mock_tree_proof_invalid")
    return proof._discard(owner_token=root_owner_token)


def consume_attempt_tree_for_publication(
    proof: AttemptTreeProof,
    *,
    root_capability: AttemptRootCapability,
    root_owner_token: object,
    binding: MockBinding,
) -> PublishReadyAttemptTreeProof:
    """Consume one exact physical tree proof for same-root publication."""

    if type(proof) is not AttemptTreeProof:
        _failure("analysis_mock_tree_proof_invalid")
    return proof._consume_for_publication(
        capability=root_capability,
        owner_token=root_owner_token,
        binding=binding,
    )


def borrow_publish_ready_attempt_root(
    proof: PublishReadyAttemptTreeProof,
    *,
    root_owner_token: object,
    binding: MockBinding,
    attempt_number: int,
) -> BorrowedPublicationRoot:
    """Lend one exact held root to the bounded publication owner once."""

    if type(proof) is not PublishReadyAttemptTreeProof:
        _failure("analysis_attempt_root_publication_invalid")
    return proof._borrow(
        owner_token=root_owner_token,
        binding=binding,
        attempt_number=attempt_number,
    )


def remove_borrowed_publication_root(
    root: BorrowedPublicationRoot,
    *,
    root_owner_token: object,
    binding: MockBinding,
    attempt_number: int,
) -> None:
    """Prove the borrowed root empty, remove it, and close its capability."""

    if type(root) is not BorrowedPublicationRoot:
        _failure("analysis_attempt_root_publication_invalid")
    root._remove(
        owner_token=root_owner_token,
        binding=binding,
        attempt_number=attempt_number,
    )


def discard_aborted_attempt_root(
    proof: AbortedAttemptTreeProof,
    *,
    root_owner_token: object | None = None,
) -> None:
    """Remove one empty pre-launch root after the lease owner accepts abort."""

    if type(proof) is not AbortedAttemptTreeProof or proof._cleanup_consumed:
        _failure("analysis_mock_abort_invalid")
    capability = proof._root_capability
    if capability.is_physical and root_owner_token is not capability._owner_token:
        _failure("analysis_attempt_root_binding_invalid")
    proof._cleanup_consumed = True
    trace = list(proof._trace)
    capability._remove_root(trace=trace)


__all__ = (
    "ALL_ATTEMPTS_BUDGET_MS",
    "ATTEMPT_BUDGET_MS",
    "AbortedAttemptTreeProof",
    "AttemptInput",
    "AttemptOutcome",
    "AttemptRootCapability",
    "AttemptTreeProof",
    "BROKER_PRIVILEGES",
    "BorrowedPublicationRoot",
    "CapabilityPreflight",
    "DiscardedAttemptTreeProof",
    "FINAL_BUDGET_MS",
    "MockBinding",
    "MockAttemptResult",
    "MockScenario",
    "NativeProcessBoundary",
    "OwnershipSnapshot",
    "PROOF_BUDGET_MS",
    "PRIVATE_SCHEMA_MAX_BYTES",
    "PublishReadyAttemptTreeProof",
    "ProcessQuarantineRequired",
    "ProcessSafetyError",
    "PreparedMockAttempt",
    "STREAM_PREFIX_MAX_BYTES",
    "SealedResult",
    "abort_prepared_mock_attempt",
    "borrow_publish_ready_attempt_root",
    "consume_attempt_tree_for_publication",
    "create_physical_attempt_root_capability",
    "discard_aborted_attempt_root",
    "discard_attempt_tree",
    "execute_prepared_mock_attempt",
    "mark_prepared_mock_attempt_recorded",
    "native_capability_preflight",
    "prepare_closed_mock_attempt",
    "prove_physical_attempt_root_capability",
    "remove_borrowed_publication_root",
)
