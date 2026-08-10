"""Closed pure bindings for bounded TG-M24.1B P1 evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn


SCHEMA_VERSION = 1
SCHEMA_NAME = "root-cause-evidence-v1"
CURRENT_CANDIDATE_ID = "current_runtime_unchanged"
STOCK_CHILD_SUBJECT = "stock_mirrored_cpython_zero_capability_lpac"
STOCK_CHILD_EXIT = "status_access_denied_0xc0000022"
COLLECTION_SCHEMA = "m241b_realtime_collection_v1"
UNKNOWN_COLLECTION_SCHEMA = "unknown"
EVIDENCE_MAX_BYTES = 8_192
INVENTORY_MAX_OBJECTS_PER_PLANE = 64

PLANE_ORDER = (
    "file_access",
    "dll_image_load",
    "registry_access",
    "code_integrity_policy",
)
OUTCOMES = frozenset({"denial", "observed_no_denial", "inconclusive"})
INCONCLUSIVE_REASONS = frozenset(
    {
        "cleanup_unproved",
        "collection_schema_unproved",
        "observation_ambiguous",
        "observation_overflow",
        "plane_scope_unproved",
        "probe_unavailable",
    }
)

_TOP_KEYS = frozenset(
    {
        "schema_version",
        "candidate_id",
        "runtime_digest",
        "subject",
        "exit_binding",
        "inventory_manifest_digest",
        "collection_proof_digest",
        "planes",
    }
)
_PLANE_KEYS = frozenset(
    {"plane", "outcome", "object_ref", "operation", "policy", "reason"}
)
_PLANE_OPERATIONS = {
    "file_access": frozenset({"file_create"}),
    "dll_image_load": frozenset({"image_map"}),
    "registry_access": frozenset(
        {"registry_open", "registry_query", "registry_query_value"}
    ),
    "code_integrity_policy": frozenset({"image_policy_validate"}),
}
_PLANE_POLICIES = {
    "file_access": frozenset({"file_io"}),
    "dll_image_load": frozenset({"image_loader"}),
    "registry_access": frozenset({"registry_access"}),
    "code_integrity_policy": frozenset({"code_integrity"}),
}
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OBJECT_REF = re.compile(r"inventory-sha256:[0-9a-f]{64}\Z")
_WINDOW_BINDING = re.compile(r"window-sha256:[0-9a-f]{64}\Z")


class RootCauseEvidenceError(ValueError):
    """Fixed failure with no rejected value or provider detail."""

    def __init__(self) -> None:
        super().__init__("root_cause_evidence_invalid")


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True, slots=True, init=False)
class StockChildSubjectProof:
    subject: str
    exit_binding: str


@dataclass(frozen=True, slots=True, init=False)
class PlaneInventory:
    plane: str
    object_refs: tuple[str, ...]
    inventory_digest: str


@dataclass(frozen=True, slots=True, init=False)
class InventoryManifest:
    runtime_digest: str
    planes: tuple[PlaneInventory, ...]
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class PlaneCollectionQualityInput:
    plane: str
    collection_schema: str
    probe_available: bool
    lossless: bool
    overflowed: bool
    plane_scope_complete: bool
    correlation_complete: bool
    cleanup_proved: bool


@dataclass(frozen=True, slots=True, init=False)
class PlaneCollectionQualityProof:
    plane: str
    subject: str
    exit_binding: str
    window_binding: str
    collection_schema: str
    probe_available: bool
    lossless: bool
    overflowed: bool
    plane_scope_complete: bool
    correlation_complete: bool
    cleanup_proved: bool
    plane_inventory_digest: str
    failure_reason: str | None


@dataclass(frozen=True, slots=True, init=False)
class CollectionQualityProof:
    runtime_digest: str
    inventory_manifest_digest: str
    subject: str
    exit_binding: str
    window_binding: str
    planes: tuple[PlaneCollectionQualityProof, ...]
    proof_digest: str


@dataclass(frozen=True, slots=True, init=False)
class PlaneEvidence:
    plane: str
    outcome: str
    object_ref: str | None
    operation: str
    policy: str
    reason: str | None


@dataclass(frozen=True, slots=True, init=False)
class RootCauseEvidence:
    schema_version: int
    candidate_id: str
    runtime_digest: str
    subject: str
    exit_binding: str
    inventory_manifest_digest: str
    collection_proof_digest: str
    collection_window_binding: str
    planes: tuple[PlaneEvidence, ...]

    @property
    def has_inconclusive(self) -> bool:
        """Whether a required plane blocks later qualification."""

        return any(plane.outcome == "inconclusive" for plane in self.planes)


def _failure() -> NoReturn:
    raise RootCauseEvidenceError() from None


def _new_model(model: type[Any], **fields: Any) -> Any:
    result = object.__new__(model)
    for name, value in fields.items():
        object.__setattr__(result, name, value)
    return result


STOCK_CHILD_ACCESS_DENIED_PROOF = _new_model(
    StockChildSubjectProof,
    subject=STOCK_CHILD_SUBJECT,
    exit_binding=STOCK_CHILD_EXIT,
)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        _failure()


def _labeled_digest(label: bytes, value: Any) -> str:
    digest = hashlib.sha256()
    digest.update(label)
    digest.update(b"\0")
    digest.update(_canonical(value))
    return "sha256:" + digest.hexdigest()


def _valid_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _valid_object_ref(value: object) -> bool:
    return type(value) is str and _OBJECT_REF.fullmatch(value) is not None


def _inventory_plane_digest(
    runtime_digest: str,
    plane: str,
    object_refs: tuple[str, ...],
) -> str:
    return _labeled_digest(
        b"taskgov-m241b-plane-inventory-v1",
        {
            "object_refs": list(object_refs),
            "plane": plane,
            "runtime_digest": runtime_digest,
        },
    )


def _manifest_digest(
    runtime_digest: str,
    planes: tuple[PlaneInventory, ...],
) -> str:
    return _labeled_digest(
        b"taskgov-m241b-inventory-manifest-v1",
        {
            "planes": [
                {
                    "inventory_digest": plane.inventory_digest,
                    "object_refs": list(plane.object_refs),
                    "plane": plane.plane,
                }
                for plane in planes
            ],
            "runtime_digest": runtime_digest,
        },
    )


def _bind_inventory_manifest_inner(
    *,
    runtime_digest: str,
    objects_by_plane: Mapping[str, Collection[str]],
) -> InventoryManifest:
    if not _valid_digest(runtime_digest) or not isinstance(objects_by_plane, Mapping):
        _failure()
    try:
        keys = tuple(objects_by_plane.keys())
    except Exception:
        _failure()
    if set(keys) != set(PLANE_ORDER) or len(keys) != len(PLANE_ORDER):
        _failure()
    planes: list[PlaneInventory] = []
    all_refs: list[str] = []
    for plane in PLANE_ORDER:
        try:
            raw_refs = objects_by_plane[plane]
            if type(raw_refs) is not tuple:
                _failure()
            refs = raw_refs
        except RootCauseEvidenceError:
            raise
        except Exception:
            _failure()
        if (
            not 1 <= len(refs) <= INVENTORY_MAX_OBJECTS_PER_PLANE
            or any(not _valid_object_ref(ref) for ref in refs)
            or len(set(refs)) != len(refs)
            or refs != tuple(sorted(refs))
        ):
            _failure()
        all_refs.extend(refs)
        planes.append(
            _new_model(
                PlaneInventory,
                plane=plane,
                object_refs=refs,
                inventory_digest=_inventory_plane_digest(
                    runtime_digest,
                    plane,
                    refs,
                ),
            )
        )
    if len(set(all_refs)) != len(all_refs):
        _failure()
    bound = tuple(planes)
    return _new_model(
        InventoryManifest,
        runtime_digest=runtime_digest,
        planes=bound,
        manifest_digest=_manifest_digest(runtime_digest, bound),
    )


def bind_inventory_manifest(
    *,
    runtime_digest: str,
    objects_by_plane: Mapping[str, Collection[str]],
) -> InventoryManifest:
    """Bind exact typed plane inventories with one fixed failure boundary."""

    try:
        return _bind_inventory_manifest_inner(
            runtime_digest=runtime_digest,
            objects_by_plane=objects_by_plane,
        )
    except RootCauseEvidenceError:
        raise
    except Exception:
        _failure()


def _validate_inventory_manifest_inner(manifest: InventoryManifest) -> None:
    if type(manifest) is not InventoryManifest or not _valid_digest(
        manifest.runtime_digest
    ):
        _failure()
    if type(manifest.planes) is not tuple or len(manifest.planes) != len(PLANE_ORDER):
        _failure()
    all_refs: list[str] = []
    for expected_plane, plane in zip(PLANE_ORDER, manifest.planes, strict=True):
        if (
            type(plane) is not PlaneInventory
            or type(plane.plane) is not str
            or plane.plane != expected_plane
            or type(plane.object_refs) is not tuple
            or not (
                1
                <= len(plane.object_refs)
                <= INVENTORY_MAX_OBJECTS_PER_PLANE
            )
            or any(not _valid_object_ref(ref) for ref in plane.object_refs)
            or len(set(plane.object_refs)) != len(plane.object_refs)
            or plane.object_refs != tuple(sorted(plane.object_refs))
            or not _valid_digest(plane.inventory_digest)
            or plane.inventory_digest
            != _inventory_plane_digest(
                manifest.runtime_digest,
                expected_plane,
                plane.object_refs,
            )
        ):
            _failure()
        all_refs.extend(plane.object_refs)
    if (
        len(set(all_refs)) != len(all_refs)
        or not _valid_digest(manifest.manifest_digest)
        or manifest.manifest_digest
        != _manifest_digest(manifest.runtime_digest, manifest.planes)
    ):
        _failure()


def _validate_inventory_manifest(manifest: InventoryManifest) -> None:
    try:
        _validate_inventory_manifest_inner(manifest)
    except RootCauseEvidenceError:
        raise
    except Exception:
        _failure()


def _quality_failure_reason(item: PlaneCollectionQualityInput) -> str | None:
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


def _collection_proof_digest(
    *,
    runtime_digest: str,
    inventory_manifest_digest: str,
    subject: str,
    exit_binding: str,
    window_binding: str,
    planes: tuple[PlaneCollectionQualityProof, ...],
) -> str:
    return _labeled_digest(
        b"taskgov-m241b-collection-proof-v1",
        {
            "exit_binding": exit_binding,
            "inventory_manifest_digest": inventory_manifest_digest,
            "planes": [
                {
                    "cleanup_proved": plane.cleanup_proved,
                    "collection_schema": plane.collection_schema,
                    "correlation_complete": plane.correlation_complete,
                    "failure_reason": plane.failure_reason,
                    "lossless": plane.lossless,
                    "overflowed": plane.overflowed,
                    "plane": plane.plane,
                    "plane_inventory_digest": plane.plane_inventory_digest,
                    "plane_scope_complete": plane.plane_scope_complete,
                    "probe_available": plane.probe_available,
                }
                for plane in planes
            ],
            "runtime_digest": runtime_digest,
            "subject": subject,
            "window_binding": window_binding,
        },
    )


def _bind_collection_quality_inner(
    *,
    subject_proof: StockChildSubjectProof,
    window_binding: str,
    inventory_manifest: InventoryManifest,
    planes: tuple[PlaneCollectionQualityInput, ...],
) -> CollectionQualityProof:
    if subject_proof is not STOCK_CHILD_ACCESS_DENIED_PROOF:
        _failure()
    _validate_inventory_manifest(inventory_manifest)
    if (
        type(window_binding) is not str
        or _WINDOW_BINDING.fullmatch(window_binding) is None
        or type(planes) is not tuple
        or len(planes) != len(PLANE_ORDER)
    ):
        _failure()
    bound: list[PlaneCollectionQualityProof] = []
    for expected_plane, item, inventory in zip(
        PLANE_ORDER,
        planes,
        inventory_manifest.planes,
        strict=True,
    ):
        if (
            type(item) is not PlaneCollectionQualityInput
            or type(item.plane) is not str
            or item.plane != expected_plane
            or type(item.collection_schema) is not str
            or item.collection_schema
            not in {COLLECTION_SCHEMA, UNKNOWN_COLLECTION_SCHEMA}
            or any(
                type(value) is not bool
                for value in (
                    item.probe_available,
                    item.lossless,
                    item.overflowed,
                    item.plane_scope_complete,
                    item.correlation_complete,
                    item.cleanup_proved,
                )
            )
        ):
            _failure()
        bound.append(
            _new_model(
                PlaneCollectionQualityProof,
                plane=expected_plane,
                subject=subject_proof.subject,
                exit_binding=subject_proof.exit_binding,
                window_binding=window_binding,
                collection_schema=item.collection_schema,
                probe_available=item.probe_available,
                lossless=item.lossless,
                overflowed=item.overflowed,
                plane_scope_complete=item.plane_scope_complete,
                correlation_complete=item.correlation_complete,
                cleanup_proved=item.cleanup_proved,
                plane_inventory_digest=inventory.inventory_digest,
                failure_reason=_quality_failure_reason(item),
            )
        )
    bound_planes = tuple(bound)
    return _new_model(
        CollectionQualityProof,
        runtime_digest=inventory_manifest.runtime_digest,
        inventory_manifest_digest=inventory_manifest.manifest_digest,
        subject=subject_proof.subject,
        exit_binding=subject_proof.exit_binding,
        window_binding=window_binding,
        planes=bound_planes,
        proof_digest=_collection_proof_digest(
            runtime_digest=inventory_manifest.runtime_digest,
            inventory_manifest_digest=inventory_manifest.manifest_digest,
            subject=subject_proof.subject,
            exit_binding=subject_proof.exit_binding,
            window_binding=window_binding,
            planes=bound_planes,
        ),
    )


def bind_collection_quality(
    *,
    subject_proof: StockChildSubjectProof,
    window_binding: str,
    inventory_manifest: InventoryManifest,
    planes: tuple[PlaneCollectionQualityInput, ...],
) -> CollectionQualityProof:
    """Bind collection quality without exposing malformed input failures."""

    try:
        return _bind_collection_quality_inner(
            subject_proof=subject_proof,
            window_binding=window_binding,
            inventory_manifest=inventory_manifest,
            planes=planes,
        )
    except RootCauseEvidenceError:
        raise
    except Exception:
        _failure()


def _proof_failure_reason(proof: PlaneCollectionQualityProof) -> str | None:
    item = PlaneCollectionQualityInput(
        plane=proof.plane,
        collection_schema=proof.collection_schema,
        probe_available=proof.probe_available,
        lossless=proof.lossless,
        overflowed=proof.overflowed,
        plane_scope_complete=proof.plane_scope_complete,
        correlation_complete=proof.correlation_complete,
        cleanup_proved=proof.cleanup_proved,
    )
    return _quality_failure_reason(item)


def _validate_collection_quality_inner(
    proof: CollectionQualityProof,
    *,
    subject_proof: StockChildSubjectProof,
    inventory_manifest: InventoryManifest,
) -> None:
    if (
        type(proof) is not CollectionQualityProof
        or subject_proof is not STOCK_CHILD_ACCESS_DENIED_PROOF
        or not _valid_digest(proof.runtime_digest)
        or not _valid_digest(proof.inventory_manifest_digest)
        or type(proof.subject) is not str
        or type(proof.exit_binding) is not str
        or proof.runtime_digest != inventory_manifest.runtime_digest
        or proof.inventory_manifest_digest != inventory_manifest.manifest_digest
        or proof.subject != subject_proof.subject
        or proof.exit_binding != subject_proof.exit_binding
        or type(proof.window_binding) is not str
        or _WINDOW_BINDING.fullmatch(proof.window_binding) is None
        or type(proof.planes) is not tuple
        or len(proof.planes) != len(PLANE_ORDER)
        or not _valid_digest(proof.proof_digest)
    ):
        _failure()
    for expected_plane, item, inventory in zip(
        PLANE_ORDER,
        proof.planes,
        inventory_manifest.planes,
        strict=True,
    ):
        if (
            type(item) is not PlaneCollectionQualityProof
            or type(item.plane) is not str
            or item.plane != expected_plane
            or type(item.subject) is not str
            or type(item.exit_binding) is not str
            or type(item.window_binding) is not str
            or item.subject != subject_proof.subject
            or item.exit_binding != subject_proof.exit_binding
            or item.window_binding != proof.window_binding
            or type(item.collection_schema) is not str
            or item.collection_schema
            not in {COLLECTION_SCHEMA, UNKNOWN_COLLECTION_SCHEMA}
            or any(
                type(value) is not bool
                for value in (
                    item.probe_available,
                    item.lossless,
                    item.overflowed,
                    item.plane_scope_complete,
                    item.correlation_complete,
                    item.cleanup_proved,
                )
            )
            or not _valid_digest(item.plane_inventory_digest)
            or item.plane_inventory_digest != inventory.inventory_digest
            or (
                item.failure_reason is not None
                and type(item.failure_reason) is not str
            )
            or item.failure_reason != _proof_failure_reason(item)
        ):
            _failure()
    if proof.proof_digest != _collection_proof_digest(
        runtime_digest=proof.runtime_digest,
        inventory_manifest_digest=proof.inventory_manifest_digest,
        subject=proof.subject,
        exit_binding=proof.exit_binding,
        window_binding=proof.window_binding,
        planes=proof.planes,
    ):
        _failure()


def _validate_collection_quality(
    proof: CollectionQualityProof,
    *,
    subject_proof: StockChildSubjectProof,
    inventory_manifest: InventoryManifest,
) -> None:
    try:
        _validate_collection_quality_inner(
            proof,
            subject_proof=subject_proof,
            inventory_manifest=inventory_manifest,
        )
    except RootCauseEvidenceError:
        raise
    except Exception:
        _failure()


def _reject_constant(_value: str) -> NoReturn:
    raise _DuplicateKey()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey()
        result[key] = value
    return result


def _decode(document: bytes) -> dict[str, Any]:
    if type(document) is not bytes or not 1 <= len(document) <= EVIDENCE_MAX_BYTES:
        _failure()
    try:
        payload = json.loads(
            document.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, OverflowError, RecursionError):
        _failure()
    if type(payload) is not dict or _canonical(payload) != document:
        _failure()
    return payload


def load_root_cause_evidence(
    document: bytes,
    *,
    expected_candidate_id: str,
    expected_runtime_digest: str,
    subject_proof: StockChildSubjectProof,
    inventory_manifest: InventoryManifest,
    collection_quality: CollectionQualityProof,
) -> RootCauseEvidence:
    """Validate one current-runtime P1 classification and trusted bindings."""

    if (
        type(expected_candidate_id) is not str
        or expected_candidate_id != CURRENT_CANDIDATE_ID
        or not _valid_digest(expected_runtime_digest)
        or subject_proof is not STOCK_CHILD_ACCESS_DENIED_PROOF
    ):
        _failure()
    _validate_inventory_manifest(inventory_manifest)
    if inventory_manifest.runtime_digest != expected_runtime_digest:
        _failure()
    _validate_collection_quality(
        collection_quality,
        subject_proof=subject_proof,
        inventory_manifest=inventory_manifest,
    )
    payload = _decode(document)
    if set(payload) != _TOP_KEYS:
        _failure()
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != SCHEMA_VERSION
        or type(payload["candidate_id"]) is not str
        or payload["candidate_id"] != expected_candidate_id
        or type(payload["runtime_digest"]) is not str
        or payload["runtime_digest"] != expected_runtime_digest
        or type(payload["subject"]) is not str
        or payload["subject"] != subject_proof.subject
        or type(payload["exit_binding"]) is not str
        or payload["exit_binding"] != subject_proof.exit_binding
        or type(payload["inventory_manifest_digest"]) is not str
        or payload["inventory_manifest_digest"]
        != inventory_manifest.manifest_digest
        or type(payload["collection_proof_digest"]) is not str
        or payload["collection_proof_digest"] != collection_quality.proof_digest
        or type(payload["planes"]) is not list
        or len(payload["planes"]) != len(PLANE_ORDER)
    ):
        _failure()

    planes: list[PlaneEvidence] = []
    for expected_plane, raw, inventory, quality in zip(
        PLANE_ORDER,
        payload["planes"],
        inventory_manifest.planes,
        collection_quality.planes,
        strict=True,
    ):
        if type(raw) is not dict or set(raw) != _PLANE_KEYS:
            _failure()
        plane = raw["plane"]
        outcome = raw["outcome"]
        object_ref = raw["object_ref"]
        operation = raw["operation"]
        policy = raw["policy"]
        reason = raw["reason"]
        if (
            type(plane) is not str
            or plane != expected_plane
            or type(outcome) is not str
            or outcome not in OUTCOMES
            or type(operation) is not str
            or operation not in _PLANE_OPERATIONS[expected_plane]
            or type(policy) is not str
            or policy not in _PLANE_POLICIES[expected_plane]
        ):
            _failure()
        if outcome == "denial":
            if (
                quality.failure_reason is not None
                or not _valid_object_ref(object_ref)
                or object_ref not in inventory.object_refs
                or reason is not None
            ):
                _failure()
        elif outcome == "observed_no_denial":
            if (
                quality.failure_reason is not None
                or object_ref is not None
                or reason is not None
            ):
                _failure()
        elif (
            object_ref is not None
            or type(reason) is not str
            or reason not in INCONCLUSIVE_REASONS
            or reason != quality.failure_reason
        ):
            _failure()
        planes.append(
            _new_model(
                PlaneEvidence,
                plane=plane,
                outcome=outcome,
                object_ref=object_ref,
                operation=operation,
                policy=policy,
                reason=reason,
            )
        )

    if not any(plane.outcome == "denial" for plane in planes):
        _failure()
    return _new_model(
        RootCauseEvidence,
        schema_version=SCHEMA_VERSION,
        candidate_id=expected_candidate_id,
        runtime_digest=expected_runtime_digest,
        subject=subject_proof.subject,
        exit_binding=subject_proof.exit_binding,
        inventory_manifest_digest=inventory_manifest.manifest_digest,
        collection_proof_digest=collection_quality.proof_digest,
        collection_window_binding=collection_quality.window_binding,
        planes=tuple(planes),
    )
