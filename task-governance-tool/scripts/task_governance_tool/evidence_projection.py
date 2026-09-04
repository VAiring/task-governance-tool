"""Pure Evidence Bundle and projection artifact construction.

SQLite repositories own persisted rows and transaction boundaries. This
module owns the byte-exact, sanitized JSON representation and later the fixed
package-local publication boundary. The pure helpers accept no path,
connection, command output, or arbitrary extension object.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from collections.abc import Mapping, Sequence
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_governance_tool.artifact_lock import (
    ArtifactLockError,
    zero_wait_artifact_lock,
)
from task_governance_tool.evidence_ledger import (
    EvidenceLedgerError,
    EvidenceSource,
)
from task_governance_tool.state_paths import (
    FileIdentity,
    StatePathError,
    ValidatedFile,
    create_exclusive_durable_file,
    inspect_physical_directory,
    inspect_physical_file,
    path_lexically_exists,
    read_physical_file_bounded,
    rename_no_replace,
    require_contained,
    unlink_validated_file,
)
from task_governance_tool.storage import (
    SCHEMA_VERSION,
    DatabaseTarget,
    EvidenceProjectionBasis,
    EvidenceProjectionState,
    ProjectMaintenanceState,
    ProjectionBundleRecord,
    StorageError,
    capture_evidence_projection_basis,
    connect_initialized_readonly,
    read_evidence_projection_state,
    read_project_maintenance,
    record_evidence_projection_outcome,
    utc_now,
    validate_utc_timestamp,
)


BUNDLE_DOMAIN = b"taskgov-completion-evidence-bundle-v1\0"
BUNDLE_V2_DOMAIN = b"taskgov-completion-evidence-bundle-v2\0"
FINDING_SNAPSHOT_DOMAIN = (
    b"taskgov-completion-bundle-finding-snapshot-v1\0"
)
INDEX_DOMAIN = b"taskgov-evidence-index-v1\0"
INDEX_V2_DOMAIN = b"taskgov-evidence-index-v2\0"

BUNDLE_MAX_BYTES = 16_777_216
INDEX_MAX_BYTES = 67_108_864
INDEX_MAX_ENTRIES = 100_000

OMISSION_ORDER = (
    "acceptance_criterion_absent",
    "verification_criterion_absent",
    "artifact_content_not_observed",
    "historical_finding_reference_absent",
)
CRITERION_KIND_ORDER = {"acceptance": 0, "verification": 1}
RELATION_ORDER = {
    "verification_attestation": 0,
    "review_assessment": 1,
    "review_finding": 2,
    "completion_basis": 3,
    "derived_analysis": 4,
    "runner_observation": 5,
}
SOURCE_KIND_ORDER = {
    "artifact_manifest": 0,
    "verification_receipt": 1,
    "review_receipt": 2,
    "review_finding": 3,
    "completion_evidence": 4,
    "derived_analysis": 5,
    "runner_observation": 6,
}

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_BUNDLE_ID = re.compile(
    r"tg_completion_evidence_bundle_[0-9a-f]{16}\Z"
)
_DECLARED_IDENTIFIER = re.compile(
    r"[a-z0-9][a-z0-9._-]{0,63}\Z"
)

_PAYLOAD_KEYS = frozenset(
    {
        "artifact_manifest",
        "authority_snapshot",
        "bundle_id",
        "bundle_version",
        "completion_cycle_id",
        "completion_evidence",
        "contract",
        "criteria",
        "criterion_links",
        "cycle_ordinal",
        "evidence_references",
        "finding_snapshots",
        "omissions",
        "project_id",
        "review_receipts",
        "sealed_at",
        "source_schema_version",
        "target",
        "task",
        "verification_receipt",
    }
)
_PAYLOAD_V2_KEYS = frozenset(
    {*_PAYLOAD_KEYS, "verification_basis", "runner_observation"}
)
_ARTIFACT_KEYS = frozenset(
    {
        "artifact_manifest_id",
        "state",
        "object_format",
        "comparison_base",
        "digest",
        "omission_code",
        "entries",
    }
)
_ARTIFACT_ENTRY_KEYS = frozenset(
    {
        "ordinal",
        "kind",
        "old_path",
        "new_path",
        "before_mode",
        "before_object_id",
        "after_mode",
        "after_object_id",
    }
)
_AUTHORITY_KEYS = frozenset(
    {"authority_snapshot_id", "generation", "digest"}
)
_COMPLETION_EVIDENCE_KEYS = frozenset(
    {
        "kind",
        "revision",
        "reason",
        "external_revision_approved",
        "completion_commit_required",
        "completion_commit_hash",
    }
)
_CONTRACT_KEYS = frozenset(
    {
        "revision",
        "specified",
        "scope",
        "acceptance",
        "constraints",
        "authority_ref",
    }
)
_CRITERION_KEYS = frozenset(
    {"criterion_id", "kind", "text", "digest"}
)
_LINK_KEYS = frozenset(
    {
        "criterion_evidence_link_id",
        "criterion_id",
        "evidence_reference_id",
        "relation",
        "assurance_class",
        "producer_class",
        "producer_version",
    }
)
_REFERENCE_KEYS = frozenset(
    {
        "evidence_reference_id",
        "source_kind",
        "source_state",
        "source_id",
        "assurance_class",
        "producer_class",
        "producer_version",
        "contract_revision",
        "authority_snapshot_id",
        "acceptance_criterion_id",
        "verification_criterion_id",
        "target_kind",
        "target_value",
        "target_base_revision",
        "target_generation",
        "completion_cycle_id",
        "digest",
    }
)
_FINDING_KEYS = frozenset(
    {
        "review_finding_id",
        "review_receipt_id",
        "target_generation",
        "severity",
        "summary",
        "status",
        "resolution_summary",
        "created_at",
        "resolved_at",
        "evidence_reference_id",
        "assurance_class",
        "producer_class",
        "producer_version",
        "digest",
    }
)
_REVIEW_KEYS = frozenset(
    {
        "review_receipt_id",
        "reviewer_key",
        "receipt_kind",
        "verdict",
        "summary",
        "user_approved",
        "created_at",
        "review_provenance",
    }
)
_TARGET_KEYS = frozenset(
    {"kind", "value", "base_revision", "generation", "capture_version"}
)
_TASK_KEYS = frozenset(
    {"task_id", "title", "description", "review_tier", "verification"}
)
_VERIFICATION_KEYS = frozenset(
    {
        "verification_receipt_id",
        "verification_subject",
        "result",
        "duration_ms",
        "scope_coverage",
        "created_at",
    }
)
_VERIFICATION_SUBJECT_KEYS = frozenset(
    {
        "basis_version",
        "kind",
        "authority_snapshot_id",
        "verification_criterion_id",
    }
)
_VERIFICATION_BASIS_KEYS = frozenset(
    {
        "basis_version",
        "kind",
        "runner_observation_id",
        "verification_receipt_id",
    }
)
_RUNNER_OBSERVATION_KEYS = frozenset(
    {
        "observation_id",
        "gate_eligibility_version",
        "route",
        "reason",
        "outcome",
        "launch_state",
        "complete_plan",
        "total_step_count",
        "completed_step_count",
        "failed_step_ordinal",
        "started_at",
        "finished_at",
        "duration_ms",
        "cpu_time_ms",
        "peak_job_memory_bytes",
        "total_process_count",
        "plan_blob_object_id",
        "plan_raw_digest",
        "plan_id",
        "plan_version",
        "plan_semantic_digest",
        "runner_implementation_version",
        "runner_implementation_digest",
        "runner_policy_digest",
        "runtime_digest",
        "sanitized_result_digest",
    }
)
_INDEX_PAYLOAD_KEYS = frozenset(
    {
        "source_schema_version",
        "project_id",
        "projection_generation",
        "bundle_count",
        "legacy_count",
        "entries",
    }
)
_INDEX_ENTRY_KEYS = frozenset(
    {
        "task_id",
        "completion_cycle_id",
        "cycle_ordinal",
        "bundle_state",
        "bundle_id",
        "bundle_file",
        "bundle_digest",
        "file_digest",
        "sealed_at",
    }
)
_INDEX_V2_ENTRY_KEYS = frozenset({*_INDEX_ENTRY_KEYS, "bundle_format_version"})


@dataclass(frozen=True)
class EvidenceProjectionError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class BundleArtifact:
    payload: dict[str, Any]
    payload_bytes: bytes
    bundle_digest: str
    envelope: dict[str, Any]
    document: bytes
    file_digest: str


@dataclass(frozen=True)
class IndexArtifact:
    payload: dict[str, Any]
    payload_bytes: bytes
    index_digest: str
    envelope: dict[str, Any]
    document: bytes


@dataclass(frozen=True)
class NativeBundlePlan:
    artifact: BundleArtifact
    storage_links: tuple[dict[str, Any], ...]
    reference_ids: tuple[str, ...]
    finding_snapshots: tuple[dict[str, Any], ...]
    omission_mask: int


@dataclass(frozen=True)
class EvidenceProjectionRefreshResult:
    code: str
    publications: int


@dataclass(frozen=True)
class _ProjectionCapture:
    maintenance: ProjectMaintenanceState
    state: EvidenceProjectionState
    basis: EvidenceProjectionBasis | None


@dataclass(frozen=True)
class _RenderedProjection:
    source_generation: int
    bundles: tuple[tuple[str, BundleArtifact], ...]
    index: IndexArtifact


class _EvidenceSourceChanged(RuntimeError):
    pass


class _EvidenceOptedOut(RuntimeError):
    pass


def _inconsistent() -> EvidenceProjectionError:
    return EvidenceProjectionError(
        "evidence_ledger_inconsistent",
        "stored evidence ledger is inconsistent",
    )


def _too_large() -> EvidenceProjectionError:
    return EvidenceProjectionError(
        "evidence_bundle_too_large",
        "completion evidence bundle exceeds the supported size",
    )


def _escape_string(value: str) -> bytes:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise _inconsistent() from exc
    escaped: list[str] = ['"']
    short = {
        '"': '\\"',
        "\\": "\\\\",
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    for character in value:
        replacement = short.get(character)
        if replacement is not None:
            escaped.append(replacement)
        elif ord(character) < 0x20:
            escaped.append(f"\\u00{ord(character):02x}")
        else:
            escaped.append(character)
    escaped.append('"')
    return "".join(escaped).encode("utf-8", errors="strict")


def canonical_json_bytes(value: Any) -> bytes:
    """Return exact integer-only canonical JSON bytes without a trailing LF."""

    active: set[int] = set()

    def encode(item: Any) -> bytes:
        if item is None:
            return b"null"
        if type(item) is bool:
            return b"true" if item else b"false"
        if type(item) is int:
            return str(item).encode("ascii")
        if type(item) is str:
            return _escape_string(item)
        if type(item) is list:
            identity = id(item)
            if identity in active:
                raise _inconsistent()
            active.add(identity)
            try:
                return (
                    b"["
                    + b",".join(encode(value) for value in item)
                    + b"]"
                )
            finally:
                active.remove(identity)
        if type(item) is dict:
            identity = id(item)
            if identity in active:
                raise _inconsistent()
            active.add(identity)
            try:
                if any(type(key) is not str for key in item):
                    raise _inconsistent()
                parts = (
                    _escape_string(key) + b":" + encode(item[key])
                    for key in sorted(item)
                )
                return b"{" + b",".join(parts) + b"}"
            finally:
                active.remove(identity)
        raise _inconsistent()

    return encode(value)


def canonical_json_document_bytes(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _domain_digest(domain: bytes, payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(domain + payload).hexdigest()


def _utf8(value: object) -> bytes:
    if type(value) is not str:
        raise _inconsistent()
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise _inconsistent() from exc


def _project_base_revision(value: object) -> str | None:
    """Map the storage empty-string sentinel to canonical JSON null."""

    if value is None or value == "":
        return None
    if type(value) is not str:
        raise _inconsistent()
    _utf8(value)
    return value


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise _inconsistent()
    return value


def _exact_integer(value: object, expected: int) -> int:
    if type(value) is not int or value != expected:
        raise _inconsistent()
    return value


def _mapping(
    value: object,
    keys: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _inconsistent()
    copied = dict(value)
    if frozenset(copied) != keys:
        raise _inconsistent()
    canonical_json_bytes(copied)
    return copied


def _runner_observation(value: object) -> dict[str, Any]:
    observation = _mapping(value, _RUNNER_OBSERVATION_KEYS)
    eligibility = observation.get("gate_eligibility_version")
    if type(eligibility) is not int or eligibility != 1:
        raise _inconsistent()
    try:
        source = EvidenceSource(
            source_kind="runner_observation",
            source_state="recorded",
            source_id=observation["observation_id"],
            source_projection=observation,
            _validated_runner_eligibility_version=1,
        )
        started_at = validate_utc_timestamp(
            observation["started_at"],
            field="verification Runner observation start time",
        )
        finished_at = validate_utc_timestamp(
            observation["finished_at"],
            field="verification Runner observation finish time",
        )
    except (EvidenceLedgerError, StorageError) as exc:
        raise _inconsistent() from exc
    if set(source.source_projection) != set(observation):
        raise _inconsistent()
    accounting = (
        observation["cpu_time_ms"],
        observation["peak_job_memory_bytes"],
        observation["total_process_count"],
    )
    total_steps = observation["total_step_count"]
    if (
        observation["route"] != "runner"
        or observation["reason"] is not None
        or observation["outcome"] != "pass"
        or observation["launch_state"] != "launched"
        or type(observation["complete_plan"]) is not int
        or observation["complete_plan"] != 1
        or type(total_steps) is not int
        or not 1 <= total_steps <= 16
        or observation["completed_step_count"] != total_steps
        or observation["failed_step_ordinal"] is not None
        or finished_at < started_at
        or type(observation["duration_ms"]) is not int
        or observation["duration_ms"] < 0
        or any(type(item) is not int or item < 0 for item in accounting)
        or observation["plan_blob_object_id"] is not None
        or type(observation["plan_id"]) is not str
        or _DECLARED_IDENTIFIER.fullmatch(observation["plan_id"]) is None
        or type(observation["plan_version"]) is not int
        or observation["plan_version"] != 1
        or observation["runner_implementation_version"]
        != "taskgov-verification-runner/1"
        or observation["runtime_digest"] is not None
    ):
        raise _inconsistent()
    return observation


def _array(value: object) -> list[Any]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
    ):
        raise _inconsistent()
    return list(value)


def finding_snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    copied = _mapping(snapshot, _FINDING_KEYS)
    observed = copied.pop("digest")
    digest = _domain_digest(
        FINDING_SNAPSHOT_DOMAIN,
        canonical_json_bytes(copied),
    )
    if observed not in {None, digest}:
        raise _inconsistent()
    return digest


def _validate_bundle_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    source_schema_version = _integer(payload["source_schema_version"])
    bundle_version = _integer(payload["bundle_version"])
    version_pair = (source_schema_version, bundle_version)
    if version_pair not in {(19, 1), (20, 2), (21, 2), (22, 2)}:
        raise _inconsistent()
    is_v2 = bundle_version == 2
    _utf8(payload["project_id"])
    _utf8(payload["completion_cycle_id"])
    _utf8(payload["sealed_at"])
    _integer(payload["cycle_ordinal"], minimum=1)
    if (
        type(payload["bundle_id"]) is not str
        or _BUNDLE_ID.fullmatch(payload["bundle_id"]) is None
    ):
        raise _inconsistent()

    artifact = _mapping(payload["artifact_manifest"], _ARTIFACT_KEYS)
    entries = [
        _mapping(item, _ARTIFACT_ENTRY_KEYS)
        for item in _array(artifact["entries"])
    ]
    entries.sort(key=lambda item: _integer(item["ordinal"]))
    if any(item["ordinal"] != ordinal for ordinal, item in enumerate(entries)):
        raise _inconsistent()
    artifact["entries"] = entries
    payload["artifact_manifest"] = artifact

    authority = _mapping(
        payload["authority_snapshot"],
        _AUTHORITY_KEYS,
    )
    _integer(authority["generation"], minimum=1)
    if (
        type(authority["digest"]) is not str
        or _SHA256.fullmatch(authority["digest"]) is None
    ):
        raise _inconsistent()
    payload["authority_snapshot"] = authority

    completion = _mapping(
        payload["completion_evidence"],
        _COMPLETION_EVIDENCE_KEYS,
    )
    if completion["kind"] not in {
        "git_commit",
        "external_revision",
        "commit_not_required",
    }:
        raise _inconsistent()
    for key in (
        "external_revision_approved",
        "completion_commit_required",
    ):
        if _integer(completion[key]) not in (0, 1):
            raise _inconsistent()
    payload["completion_evidence"] = completion

    contract = _mapping(payload["contract"], _CONTRACT_KEYS)
    _integer(contract["revision"])
    if type(contract["specified"]) is not bool:
        raise _inconsistent()
    payload["contract"] = contract

    criteria = [
        _mapping(item, _CRITERION_KEYS)
        for item in _array(payload["criteria"])
    ]
    if any(item["kind"] not in CRITERION_KIND_ORDER for item in criteria):
        raise _inconsistent()
    criteria.sort(
        key=lambda item: (
            CRITERION_KIND_ORDER[item["kind"]],
            _utf8(item["criterion_id"]),
        )
    )
    if len({item["criterion_id"] for item in criteria}) != len(criteria):
        raise _inconsistent()
    payload["criteria"] = criteria
    criterion_kinds = {
        item["criterion_id"]: item["kind"] for item in criteria
    }

    links = [
        _mapping(item, _LINK_KEYS)
        for item in _array(payload["criterion_links"])
    ]
    for item in links:
        if (
            item["criterion_id"] not in criterion_kinds
            or item["relation"] not in RELATION_ORDER
        ):
            raise _inconsistent()
        _integer(item["producer_version"], minimum=1)
    links.sort(
        key=lambda item: (
            CRITERION_KIND_ORDER[
                criterion_kinds[item["criterion_id"]]
            ],
            _utf8(item["criterion_id"]),
            RELATION_ORDER[item["relation"]],
            _utf8(item["evidence_reference_id"]),
            _utf8(item["criterion_evidence_link_id"]),
        )
    )
    payload["criterion_links"] = links

    references = [
        _mapping(item, _REFERENCE_KEYS)
        for item in _array(payload["evidence_references"])
    ]
    for item in references:
        if item["source_kind"] not in SOURCE_KIND_ORDER:
            raise _inconsistent()
        _integer(item["producer_version"], minimum=1)
        _integer(item["contract_revision"])
        _integer(item["target_generation"], minimum=1)
        item["target_base_revision"] = _project_base_revision(
            item["target_base_revision"]
        )
        if (
            type(item["digest"]) is not str
            or _SHA256.fullmatch(item["digest"]) is None
        ):
            raise _inconsistent()
    references.sort(
        key=lambda item: (
            SOURCE_KIND_ORDER[item["source_kind"]],
            _utf8(item["source_id"]),
            _utf8(item["evidence_reference_id"]),
        )
    )
    payload["evidence_references"] = references
    findings = [
        _mapping(item, _FINDING_KEYS)
        for item in _array(payload["finding_snapshots"])
    ]
    for item in findings:
        _integer(item["target_generation"], minimum=1)
        _integer(item["producer_version"], minimum=1)
        if finding_snapshot_digest(item) != item["digest"]:
            raise _inconsistent()
    findings.sort(
        key=lambda item: (
            item["target_generation"],
            _utf8(item["created_at"]),
            _utf8(item["review_finding_id"]),
        )
    )
    payload["finding_snapshots"] = findings

    omissions = _array(payload["omissions"])
    if any(
        type(item) is not str or item not in OMISSION_ORDER
        for item in omissions
    ):
        raise _inconsistent()
    if len(set(omissions)) != len(omissions):
        raise _inconsistent()
    payload["omissions"] = [
        item for item in OMISSION_ORDER if item in omissions
    ]

    reviews = [
        _mapping(item, _REVIEW_KEYS)
        for item in _array(payload["review_receipts"])
    ]
    for item in reviews:
        if _integer(item["user_approved"]) not in (0, 1):
            raise _inconsistent()
        provenance = item["review_provenance"]
        if provenance is not None:
            canonical_json_bytes(provenance)
            if not isinstance(provenance, dict):
                raise _inconsistent()
            _exact_integer(provenance.get("provenance_version"), 1)
    payload["review_receipts"] = reviews

    target = _mapping(payload["target"], _TARGET_KEYS)
    _exact_integer(target["capture_version"], 1)
    _integer(target["generation"], minimum=1)
    target["base_revision"] = _project_base_revision(
        target["base_revision"]
    )
    payload["target"] = target

    task = _mapping(payload["task"], _TASK_KEYS)
    if _integer(task["review_tier"]) not in (0, 1, 2):
        raise _inconsistent()
    payload["task"] = task

    verification = payload["verification_receipt"]
    if verification is not None:
        verification = _mapping(
            verification,
            _VERIFICATION_KEYS,
        )
        subject = _mapping(
            verification["verification_subject"],
            _VERIFICATION_SUBJECT_KEYS,
        )
        _exact_integer(subject["basis_version"], 1)
        if subject["kind"] != "task_verification_criterion":
            raise _inconsistent()
        if (
            verification["result"] not in {"pass", "fail", "timeout"}
            or verification["scope_coverage"]
            not in {"full", "partial"}
        ):
            raise _inconsistent()
        _integer(verification["duration_ms"])
        verification["verification_subject"] = subject
    payload["verification_receipt"] = verification
    if is_v2:
        verification_basis = _mapping(
            payload["verification_basis"],
            _VERIFICATION_BASIS_KEYS,
        )
        _exact_integer(verification_basis["basis_version"], 1)
        kind = verification_basis["kind"]
        receipt_id = (
            verification["verification_receipt_id"]
            if verification is not None
            else None
        )
        runner_references = [
            item for item in references
            if item["source_kind"] == "runner_observation"
        ]
        runner_links = [
            item for item in links
            if item["relation"] == "runner_observation"
        ]
        if kind in {"caller_attestation", "not_required"}:
            if (
                verification_basis["runner_observation_id"] is not None
                or payload["runner_observation"] is not None
                or verification_basis["verification_receipt_id"] != receipt_id
                or runner_references
                or runner_links
                or (
                    kind == "caller_attestation"
                    and verification is None
                )
                or (
                    kind == "not_required"
                    and verification is not None
                )
            ):
                raise _inconsistent()
        elif kind == "runner_observation":
            runner = _runner_observation(payload["runner_observation"])
            runner_id = verification_basis["runner_observation_id"]
            if (
                source_schema_version not in {21, 22}
                or verification is not None
                or verification_basis["verification_receipt_id"] is not None
                or type(runner_id) is not str
                or runner_id != runner["observation_id"]
                or runner["gate_eligibility_version"] != 1
                or len(runner_references) != 1
                or len(runner_links) != 1
            ):
                raise _inconsistent()
            reference = runner_references[0]
            link = runner_links[0]
            if (
                reference["source_state"] != "recorded"
                or reference["source_id"] != runner_id
                or reference["assurance_class"] != "machine_observed"
                or reference["producer_class"] != "verification_runner"
                or reference["completion_cycle_id"] is not None
                or link["evidence_reference_id"]
                != reference["evidence_reference_id"]
                or criterion_kinds.get(link["criterion_id"])
                != "verification"
            ):
                raise _inconsistent()
            payload["runner_observation"] = runner
        else:
            raise _inconsistent()
        payload["verification_basis"] = verification_basis
    canonical_json_bytes(payload)
    return payload


def assemble_bundle_payload(
    basis: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(basis, Mapping):
        raise _inconsistent()
    version_pair = (
        basis.get("source_schema_version"),
        basis.get("bundle_version"),
    )
    keys = (
        _PAYLOAD_V2_KEYS
        if version_pair in {(20, 2), (21, 2), (22, 2)}
        else _PAYLOAD_KEYS
    )
    payload = _mapping(basis, keys)
    return _validate_bundle_payload(payload)


def _bundle_domain_and_format(payload: Mapping[str, Any]) -> tuple[bytes, int]:
    if (
        payload["source_schema_version"],
        payload["bundle_version"],
    ) == (19, 1):
        return BUNDLE_DOMAIN, 1
    if (
        payload["source_schema_version"],
        payload["bundle_version"],
    ) in {(20, 2), (21, 2), (22, 2)}:
        return BUNDLE_V2_DOMAIN, 2
    raise _inconsistent()


def bundle_payload_digest(payload: Mapping[str, Any]) -> str:
    normalized = assemble_bundle_payload(payload)
    encoded = canonical_json_bytes(normalized)
    if len(encoded) > BUNDLE_MAX_BYTES:
        raise _too_large()
    domain, _ = _bundle_domain_and_format(normalized)
    return _domain_digest(domain, encoded)


def build_bundle_artifact(
    basis: Mapping[str, Any],
) -> BundleArtifact:
    payload = assemble_bundle_payload(basis)
    payload_bytes = canonical_json_bytes(payload)
    if len(payload_bytes) > BUNDLE_MAX_BYTES:
        raise _too_large()
    domain, format_version = _bundle_domain_and_format(payload)
    digest = _domain_digest(domain, payload_bytes)
    envelope = {
        "bundle_digest": digest,
        "format_version": format_version,
        "payload": payload,
    }
    document = canonical_json_document_bytes(envelope)
    if len(document) > BUNDLE_MAX_BYTES:
        raise _too_large()
    return BundleArtifact(
        payload=payload,
        payload_bytes=payload_bytes,
        bundle_digest=digest,
        envelope=envelope,
        document=document,
        file_digest="sha256:" + hashlib.sha256(document).hexdigest(),
    )


def _reference_projection(reference: Mapping[str, Any]) -> dict[str, Any]:
    projected = {
        key: reference[key]
        for key in _REFERENCE_KEYS
    }
    projected["target_base_revision"] = _project_base_revision(
        projected["target_base_revision"]
    )
    return projected


def _criterion_kind_map(
    criteria: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in criteria:
        criterion_id = row["criterion_id"]
        kind = row["criterion_kind"]
        if (
            type(criterion_id) is not str
            or kind not in CRITERION_KIND_ORDER
            or criterion_id in result
        ):
            raise _inconsistent()
        result[criterion_id] = kind
    return result


def required_native_bundle_link_count(*, basis: Any, cycle: Any) -> int:
    criterion_kinds = _criterion_kind_map(tuple(basis.criteria))
    runner_observation = getattr(basis, "runner_observation", None)
    runner_reference = getattr(basis, "runner_reference", None)
    runner_criterion_link = getattr(basis, "runner_criterion_link", None)
    count = 0
    if "acceptance" in criterion_kinds.values():
        count = 2 + len(tuple(basis.review_references))
        count += sum(
            reference is not None
            and finding["target_generation"]
            == cycle.review_target_generation
            for finding, reference in zip(
                basis.findings,
                basis.finding_references,
                strict=True,
            )
        )
    if "verification" in criterion_kinds.values():
        if cycle.verification_basis_kind == "runner_observation":
            if (
                basis.verification_reference is not None
                or runner_observation is None
                or runner_reference is None
                or runner_criterion_link is None
            ):
                raise _inconsistent()
        else:
            if (
                basis.verification_reference is None
                or runner_observation is not None
                or runner_reference is not None
                or runner_criterion_link is not None
            ):
                raise _inconsistent()
            count += 1
    elif any(
        value is not None
        for value in (
            basis.verification_reference,
            runner_observation,
            runner_reference,
            runner_criterion_link,
        )
    ):
        raise _inconsistent()
    return count


def build_native_bundle_plan(
    *,
    basis: Any,
    cycle: Any,
    completion_identity: Any,
    criterion_link_ids: Sequence[str],
    sealed_at: str,
) -> NativeBundlePlan:
    """Build one native Bundle from repository-validated seal-time rows."""

    criteria_rows = tuple(basis.criteria)
    criterion_kinds = _criterion_kind_map(criteria_rows)
    runner_observation = getattr(basis, "runner_observation", None)
    runner_reference = getattr(basis, "runner_reference", None)
    runner_criterion_link = getattr(basis, "runner_criterion_link", None)
    runner_observation_id = getattr(
        cycle,
        "verification_runner_observation_id",
        None,
    )
    acceptance_id = next(
        (
            criterion_id
            for criterion_id, kind in criterion_kinds.items()
            if kind == "acceptance"
        ),
        None,
    )
    verification_id = next(
        (
            criterion_id
            for criterion_id, kind in criterion_kinds.items()
            if kind == "verification"
        ),
        None,
    )

    references = [
        dict(basis.artifact_reference),
        *(
            [dict(basis.verification_reference)]
            if basis.verification_reference is not None
            else []
        ),
        *(
            [dict(runner_reference)]
            if runner_reference is not None
            else []
        ),
        *(dict(value) for value in basis.review_references),
        *(
            dict(value)
            for value in basis.finding_references
            if value is not None
        ),
        dict(basis.completion_reference),
    ]
    reference_by_id: dict[str, dict[str, Any]] = {}
    for reference in references:
        reference_id = reference.get("evidence_reference_id")
        if type(reference_id) is not str or reference_id in reference_by_id:
            raise _inconsistent()
        reference_by_id[reference_id] = reference

    relation_specs: list[tuple[str, str, str]] = []
    if acceptance_id is not None:
        relation_specs.extend(
            (
                (
                    acceptance_id,
                    basis.artifact_reference["evidence_reference_id"],
                    "completion_basis",
                ),
                (
                    acceptance_id,
                    basis.completion_reference["evidence_reference_id"],
                    "completion_basis",
                ),
            )
        )
        relation_specs.extend(
            (
                acceptance_id,
                reference["evidence_reference_id"],
                "review_assessment",
            )
            for reference in basis.review_references
        )
        relation_specs.extend(
            (
                acceptance_id,
                reference["evidence_reference_id"],
                "review_finding",
            )
            for finding, reference in zip(
                basis.findings,
                basis.finding_references,
                strict=True,
            )
            if (
                reference is not None
                and finding["target_generation"]
                == cycle.review_target_generation
            )
        )
    if verification_id is not None:
        if cycle.verification_basis_kind == "runner_observation":
            runner_link = runner_criterion_link
            if (
                basis.verification_reference is not None
                or runner_observation is None
                or not isinstance(runner_reference, Mapping)
                or not isinstance(runner_link, Mapping)
                or runner_link.get("project_id") != cycle.project_id
                or runner_link.get("task_id") != cycle.task_id
                or runner_link.get("criterion_id") != verification_id
                or runner_link.get("evidence_reference_id")
                != runner_reference.get("evidence_reference_id")
                or runner_link.get("relation") != "runner_observation"
            ):
                raise _inconsistent()
        else:
            if (
                basis.verification_reference is None
                or runner_observation is not None
                or runner_reference is not None
                or runner_criterion_link is not None
            ):
                raise _inconsistent()
            relation_specs.append(
                (
                    verification_id,
                    basis.verification_reference["evidence_reference_id"],
                    "verification_attestation",
                )
            )
    relation_specs.sort(
        key=lambda item: (
            CRITERION_KIND_ORDER[criterion_kinds[item[0]]],
            _utf8(item[0]),
            RELATION_ORDER[item[2]],
            _utf8(item[1]),
        )
    )
    link_ids = tuple(criterion_link_ids)
    if (
        len(link_ids) != len(relation_specs)
        or len(set(link_ids)) != len(link_ids)
        or any(type(value) is not str for value in link_ids)
    ):
        raise _inconsistent()
    storage_links: list[dict[str, Any]] = []
    payload_links: list[dict[str, Any]] = []
    if cycle.verification_basis_kind == "runner_observation":
        stored_runner_link = dict(runner_criterion_link)
        payload_runner_link = {
            key: stored_runner_link[key]
            for key in (
                "criterion_evidence_link_id",
                "criterion_id",
                "evidence_reference_id",
                "relation",
                "assurance_class",
                "producer_class",
                "producer_version",
            )
        }
        storage_links.append(stored_runner_link)
        payload_links.append(payload_runner_link)
    for link_id, (criterion_id, reference_id, relation) in zip(
        link_ids,
        relation_specs,
        strict=True,
    ):
        reference = reference_by_id[reference_id]
        payload_link = {
            "criterion_evidence_link_id": link_id,
            "criterion_id": criterion_id,
            "evidence_reference_id": reference_id,
            "relation": relation,
            "assurance_class": reference["assurance_class"],
            "producer_class": reference["producer_class"],
            "producer_version": reference["producer_version"],
        }
        payload_links.append(payload_link)
        storage_links.append(
            {
                **payload_link,
                "project_id": cycle.project_id,
                "task_id": cycle.task_id,
                "created_at": sealed_at,
            }
        )
    link_sort_key = lambda item: (
        CRITERION_KIND_ORDER[criterion_kinds[item["criterion_id"]]],
        _utf8(item["criterion_id"]),
        RELATION_ORDER[item["relation"]],
        _utf8(item["evidence_reference_id"]),
    )
    payload_links.sort(key=link_sort_key)
    storage_links.sort(key=link_sort_key)

    snapshots: list[dict[str, Any]] = []
    historical_reference_absent = False
    for finding, reference in zip(
        basis.findings,
        basis.finding_references,
        strict=True,
    ):
        if reference is None:
            evidence_reference_id = None
            assurance_class = "legacy_unknown"
            producer_class = "legacy_migration"
            historical_reference_absent = True
        else:
            evidence_reference_id = reference["evidence_reference_id"]
            assurance_class = reference["assurance_class"]
            producer_class = reference["producer_class"]
        snapshot = {
            "review_finding_id": finding["review_finding_id"],
            "review_receipt_id": finding["review_receipt_id"],
            "target_generation": finding["target_generation"],
            "severity": finding["severity"],
            "summary": finding["summary"],
            "status": finding["status"],
            "resolution_summary": finding["resolution_summary"],
            "created_at": finding["created_at"],
            "resolved_at": finding["resolved_at"],
            "evidence_reference_id": evidence_reference_id,
            "assurance_class": assurance_class,
            "producer_class": producer_class,
            "producer_version": 1,
            "digest": None,
        }
        snapshot["digest"] = finding_snapshot_digest(snapshot)
        snapshots.append(snapshot)

    manifest = basis.artifact_manifest
    artifact_entries = [
        {
            "ordinal": entry["ordinal"],
            "kind": entry["entry_kind"],
            "old_path": entry["old_path"],
            "new_path": entry["new_path"],
            "before_mode": entry["before_mode"],
            "before_object_id": entry["before_object_id"],
            "after_mode": entry["after_mode"],
            "after_object_id": entry["after_object_id"],
        }
        for entry in basis.artifact_entries
    ]
    snapshot = basis.authority_snapshot
    review_receipts = [
        {
            "review_receipt_id": value["receipt"]["review_receipt_id"],
            "reviewer_key": value["receipt"]["reviewer_key"],
            "receipt_kind": value["receipt"]["receipt_kind"],
            "verdict": value["receipt"]["verdict"],
            "summary": value["receipt"]["summary"],
            "user_approved": value["receipt"]["user_approved"],
            "created_at": value["receipt"]["created_at"],
            "review_provenance": value["provenance"],
        }
        for value in basis.review_receipts
    ]

    omissions = []
    if acceptance_id is None:
        omissions.append("acceptance_criterion_absent")
    if verification_id is None:
        omissions.append("verification_criterion_absent")
    if manifest["omission_code"] == "artifact_content_not_observed":
        omissions.append("artifact_content_not_observed")
    elif manifest["omission_code"] is not None:
        raise _inconsistent()
    if historical_reference_absent:
        omissions.append("historical_finding_reference_absent")

    payload = {
        "artifact_manifest": {
            "artifact_manifest_id": manifest["artifact_manifest_id"],
            "state": manifest["state"],
            "object_format": manifest["object_format"],
            "comparison_base": manifest["comparison_base"],
            "digest": manifest["digest"],
            "omission_code": manifest["omission_code"],
            "entries": artifact_entries,
        },
        "authority_snapshot": {
            "authority_snapshot_id": snapshot["authority_snapshot_id"],
            "generation": snapshot["generation"],
            "digest": snapshot["basis_digest"],
        },
        "bundle_id": completion_identity.completion_evidence_bundle_id,
        "bundle_version": 2,
        "completion_cycle_id": completion_identity.completion_cycle_id,
        "cycle_ordinal": completion_identity.saved_cycle_ordinal,
        "sealed_at": sealed_at,
        "completion_evidence": {
            "kind": cycle.completion_evidence_kind,
            "revision": cycle.completion_evidence_revision,
            "reason": cycle.completion_evidence_reason,
            "external_revision_approved": int(
                cycle.external_revision_approved
            ),
            "completion_commit_required": int(
                cycle.completion_commit_required
            ),
            "completion_commit_hash": cycle.completion_commit_hash,
        },
        "contract": {
            "revision": snapshot["contract_revision"],
            "specified": snapshot["contract_state"]
            == "contract_specified",
            "scope": snapshot["contract_scope"],
            "acceptance": snapshot["contract_acceptance"],
            "constraints": snapshot["contract_constraints"],
            "authority_ref": snapshot["contract_authority_ref"],
        },
        "criteria": [
            {
                "criterion_id": row["criterion_id"],
                "kind": row["criterion_kind"],
                "text": row["criterion_text"],
                "digest": row["digest"],
            }
            for row in criteria_rows
        ],
        "criterion_links": payload_links,
        "evidence_references": [
            _reference_projection(value)
            for value in references
        ],
        "finding_snapshots": snapshots,
        "omissions": omissions,
        "project_id": cycle.project_id,
        "review_receipts": review_receipts,
        "source_schema_version": SCHEMA_VERSION,
        "target": {
            "kind": cycle.review_target_kind,
            "value": cycle.review_target_value,
            "base_revision": _project_base_revision(
                cycle.review_target_base_revision
            ),
            "generation": cycle.review_target_generation,
            "capture_version": 1,
        },
        "task": dict(basis.task),
        "verification_receipt": (
            dict(basis.verification_receipt)
            if basis.verification_receipt is not None
            else None
        ),
        "verification_basis": {
            "basis_version": 1,
            "kind": cycle.verification_basis_kind,
            "runner_observation_id": runner_observation_id,
            "verification_receipt_id": cycle.verification_receipt_id,
        },
        "runner_observation": (
            dict(runner_observation)
            if runner_observation is not None
            else None
        ),
    }
    artifact = build_bundle_artifact(payload)
    reference_ids = tuple(
        item["evidence_reference_id"]
        for item in artifact.payload["evidence_references"]
    )
    ordered_link_ids = tuple(
        item["criterion_evidence_link_id"]
        for item in artifact.payload["criterion_links"]
    )
    storage_by_id = {
        item["criterion_evidence_link_id"]: item
        for item in storage_links
    }
    omission_mask = sum(
        1 << OMISSION_ORDER.index(value)
        for value in artifact.payload["omissions"]
    )
    return NativeBundlePlan(
        artifact=artifact,
        storage_links=tuple(
            storage_by_id[link_id] for link_id in ordered_link_ids
        ),
        reference_ids=reference_ids,
        finding_snapshots=tuple(
            dict(value) for value in artifact.payload["finding_snapshots"]
        ),
        omission_mask=omission_mask,
    )


def _validate_index_entry(
    entry: dict[str, Any],
    *,
    index_format_version: int,
) -> dict[str, Any]:
    _utf8(entry["task_id"])
    _utf8(entry["completion_cycle_id"])
    _integer(entry["cycle_ordinal"], minimum=1)
    state = entry["bundle_state"]
    identity_fields = (
        "bundle_id",
        "bundle_file",
        "bundle_digest",
        "file_digest",
        "sealed_at",
    )
    if index_format_version == 2:
        identity_fields = (*identity_fields, "bundle_format_version")
    if state == "legacy_unknown":
        if any(entry[field] is not None for field in identity_fields):
            raise _inconsistent()
    elif state == "native":
        string_identity_fields = tuple(
            field for field in identity_fields if field != "bundle_format_version"
        )
        if any(type(entry[field]) is not str for field in string_identity_fields):
            raise _inconsistent()
        if (
            index_format_version == 2
            and _integer(entry["bundle_format_version"]) not in {1, 2}
        ):
            raise _inconsistent()
        bundle_id = entry["bundle_id"]
        if (
            _BUNDLE_ID.fullmatch(bundle_id) is None
            or entry["bundle_file"] != f"bundles/{bundle_id}.json"
        ):
            raise _inconsistent()
        if (
            _SHA256.fullmatch(entry["bundle_digest"]) is None
            or _SHA256.fullmatch(entry["file_digest"]) is None
        ):
            raise _inconsistent()
    else:
        raise _inconsistent()
    return entry


def assemble_index_payload(
    basis: Mapping[str, Any],
) -> dict[str, Any]:
    payload = _mapping(basis, _INDEX_PAYLOAD_KEYS)
    source_schema_version = _integer(payload["source_schema_version"])
    if source_schema_version not in {19, 20, 21, 22}:
        raise _inconsistent()
    index_format_version = 1 if source_schema_version == 19 else 2
    _utf8(payload["project_id"])
    _integer(payload["projection_generation"])
    _integer(payload["bundle_count"])
    _integer(payload["legacy_count"])
    entries = [
        _validate_index_entry(
            _mapping(
                item,
                _INDEX_ENTRY_KEYS
                if index_format_version == 1
                else _INDEX_V2_ENTRY_KEYS,
            ),
            index_format_version=index_format_version,
        )
        for item in _array(payload["entries"])
    ]
    if len(entries) > INDEX_MAX_ENTRIES:
        raise EvidenceProjectionError(
            "evidence_projection_too_large",
            "Evidence projection exceeds the supported size",
        )
    entries.sort(
        key=lambda item: (
            _utf8(item["task_id"]),
            item["cycle_ordinal"],
            _utf8(item["completion_cycle_id"]),
        )
    )
    native_count = sum(
        item["bundle_state"] == "native" for item in entries
    )
    legacy_count = len(entries) - native_count
    if (
        payload["bundle_count"] != native_count
        or payload["legacy_count"] != legacy_count
    ):
        raise _inconsistent()
    payload["entries"] = entries
    canonical_json_bytes(payload)
    return payload


def index_payload_digest(payload: Mapping[str, Any]) -> str:
    normalized = assemble_index_payload(payload)
    domain = (
        INDEX_DOMAIN
        if normalized["source_schema_version"] == 19
        else INDEX_V2_DOMAIN
    )
    return _domain_digest(
        domain,
        canonical_json_bytes(normalized),
    )


def build_index_artifact(
    basis: Mapping[str, Any],
) -> IndexArtifact:
    payload = assemble_index_payload(basis)
    payload_bytes = canonical_json_bytes(payload)
    format_version = 1 if payload["source_schema_version"] == 19 else 2
    domain = INDEX_DOMAIN if format_version == 1 else INDEX_V2_DOMAIN
    digest = _domain_digest(domain, payload_bytes)
    envelope = {
        "format_version": format_version,
        "index_digest": digest,
        "payload": payload,
    }
    document = canonical_json_document_bytes(envelope)
    if len(document) > INDEX_MAX_BYTES:
        raise EvidenceProjectionError(
            "evidence_projection_too_large",
            "Evidence projection exceeds the supported size",
        )
    return IndexArtifact(
        payload=payload,
        payload_bytes=payload_bytes,
        index_digest=digest,
        envelope=envelope,
        document=document,
    )


MAX_PUBLICATIONS_PER_ATTEMPT = 2
_INDEX_TEMP_PREFIX = ".taskgov-evidence-index-"
_BUNDLE_TEMP_PREFIX = ".taskgov-evidence-bundle-"
_TEMP_SUFFIX = ".tmp"


def _decode_omission_mask(mask: object) -> list[str]:
    if (
        type(mask) is not int
        or mask < 0
        or mask >= (1 << len(OMISSION_ORDER))
    ):
        raise _inconsistent()
    return [
        value
        for ordinal, value in enumerate(OMISSION_ORDER)
        if mask & (1 << ordinal)
    ]


def build_projection_bundle_artifact(
    record: ProjectionBundleRecord,
) -> BundleArtifact:
    """Purely reconstruct and validate one stored native Bundle record."""

    if not isinstance(record, ProjectionBundleRecord):
        raise _inconsistent()
    try:
        return _build_projection_bundle_artifact(record)
    except EvidenceProjectionError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise _inconsistent() from exc


def _build_projection_bundle_artifact(
    record: ProjectionBundleRecord,
) -> BundleArtifact:
    bundle = record.bundle
    cycle = record.cycle
    _exact_integer(cycle.evidence_basis_version, 1)
    if (
        cycle.completion_cycle_id != bundle.completion_cycle_id
        or cycle.project_id != bundle.project_id
        or cycle.task_id != bundle.task_id
        or cycle.saved_cycle_ordinal != bundle.cycle_ordinal
        or cycle.completion_evidence_bundle_id
        != bundle.completion_evidence_bundle_id
        or record.task.get("task_id") != bundle.task_id
        or record.artifact_manifest.get("artifact_manifest_id")
        != bundle.artifact_manifest_id
    ):
        raise _inconsistent()

    manifest = record.artifact_manifest
    snapshot = record.authority_snapshot
    review_receipts = []
    for selected in record.review_receipts:
        if not isinstance(selected, Mapping):
            raise _inconsistent()
        receipt = selected.get("receipt")
        if not isinstance(receipt, Mapping) or set(selected) != {
            "receipt",
            "provenance",
        }:
            raise _inconsistent()
        review_receipts.append(
            {
                "review_receipt_id": receipt["review_receipt_id"],
                "reviewer_key": receipt["reviewer_key"],
                "receipt_kind": receipt["receipt_kind"],
                "verdict": receipt["verdict"],
                "summary": receipt["summary"],
                "user_approved": receipt["user_approved"],
                "created_at": receipt["created_at"],
                "review_provenance": selected["provenance"],
            }
        )

    payload = {
        "artifact_manifest": {
            "artifact_manifest_id": manifest["artifact_manifest_id"],
            "state": manifest["state"],
            "object_format": manifest["object_format"],
            "comparison_base": manifest["comparison_base"],
            "digest": manifest["digest"],
            "omission_code": manifest["omission_code"],
            "entries": [
                {
                    "ordinal": entry["ordinal"],
                    "kind": entry["entry_kind"],
                    "old_path": entry["old_path"],
                    "new_path": entry["new_path"],
                    "before_mode": entry["before_mode"],
                    "before_object_id": entry["before_object_id"],
                    "after_mode": entry["after_mode"],
                    "after_object_id": entry["after_object_id"],
                }
                for entry in record.artifact_entries
            ],
        },
        "authority_snapshot": {
            "authority_snapshot_id": snapshot["authority_snapshot_id"],
            "generation": snapshot["generation"],
            "digest": snapshot["basis_digest"],
        },
        "bundle_id": bundle.completion_evidence_bundle_id,
        "bundle_version": bundle.bundle_version,
        "completion_cycle_id": cycle.completion_cycle_id,
        "cycle_ordinal": cycle.saved_cycle_ordinal,
        "sealed_at": bundle.sealed_at,
        "completion_evidence": {
            "kind": cycle.completion_evidence_kind,
            "revision": cycle.completion_evidence_revision,
            "reason": cycle.completion_evidence_reason,
            "external_revision_approved": int(
                cycle.external_revision_approved
            ),
            "completion_commit_required": int(
                cycle.completion_commit_required
            ),
            "completion_commit_hash": cycle.completion_commit_hash,
        },
        "contract": {
            "revision": snapshot["contract_revision"],
            "specified": snapshot["contract_state"]
            == "contract_specified",
            "scope": snapshot["contract_scope"],
            "acceptance": snapshot["contract_acceptance"],
            "constraints": snapshot["contract_constraints"],
            "authority_ref": snapshot["contract_authority_ref"],
        },
        "criteria": [
            {
                "criterion_id": criterion["criterion_id"],
                "kind": criterion["criterion_kind"],
                "text": criterion["criterion_text"],
                "digest": criterion["digest"],
            }
            for criterion in record.criteria
        ],
        "criterion_links": [
            {
                "criterion_evidence_link_id": (
                    link.criterion_evidence_link_id
                ),
                "criterion_id": link.criterion_id,
                "evidence_reference_id": link.evidence_reference_id,
                "relation": link.relation,
                "assurance_class": link.assurance_class,
                "producer_class": link.producer_class,
                "producer_version": link.producer_version,
            }
            for link in bundle.criterion_links
        ],
        "evidence_references": [
            _reference_projection(reference)
            for reference in record.evidence_references
        ],
        "finding_snapshots": [
            {
                "review_finding_id": finding.review_finding_id,
                "review_receipt_id": finding.review_receipt_id,
                "target_generation": finding.target_generation,
                "severity": finding.severity,
                "summary": finding.summary,
                "status": finding.status,
                "resolution_summary": finding.resolution_summary,
                "created_at": finding.created_at,
                "resolved_at": finding.resolved_at,
                "evidence_reference_id": finding.evidence_reference_id,
                "assurance_class": finding.assurance_class,
                "producer_class": finding.producer_class,
                "producer_version": finding.producer_version,
                "digest": finding.digest,
            }
            for finding in record.finding_snapshots
        ],
        "omissions": _decode_omission_mask(bundle.omission_mask),
        "project_id": bundle.project_id,
        "review_receipts": review_receipts,
        "source_schema_version": bundle.source_schema_version,
        "target": {
            "kind": bundle.target_kind,
            "value": bundle.target_value,
            "base_revision": _project_base_revision(
                bundle.target_base_revision
            ),
            "generation": bundle.target_generation,
            "capture_version": bundle.target_capture_version,
        },
        "task": dict(record.task),
        "verification_receipt": (
            dict(record.verification_receipt)
            if record.verification_receipt is not None
            else None
        ),
    }
    version_pair = (bundle.source_schema_version, bundle.bundle_version)
    if version_pair in {(20, 2), (21, 2), (22, 2)}:
        payload["verification_basis"] = {
            "basis_version": 1,
            "kind": bundle.verification_basis_kind,
            "runner_observation_id": (
                bundle.verification_runner_observation_id
            ),
            "verification_receipt_id": bundle.verification_receipt_id,
        }
        payload["runner_observation"] = record.runner_observation
    elif version_pair != (19, 1):
        raise _inconsistent()
    artifact = build_bundle_artifact(payload)
    if (
        artifact.bundle_digest != bundle.bundle_digest
        or len(artifact.payload_bytes) != bundle.payload_size_bytes
        or len(artifact.document) > BUNDLE_MAX_BYTES
    ):
        raise _inconsistent()
    return artifact


def _render_projection(
    basis: EvidenceProjectionBasis,
) -> _RenderedProjection:
    if (
        type(basis.project_id) is not str
        or type(basis.source_generation) is not int
        or basis.source_generation < 0
    ):
        raise _inconsistent()
    if _integer(basis.source_schema_version) not in {SCHEMA_VERSION, 22}:
        raise _inconsistent()

    bundle_rows = {
        bundle.completion_evidence_bundle_id: bundle
        for bundle in basis.bundles
    }
    if len(bundle_rows) != len(basis.bundles):
        raise _inconsistent()
    rendered_by_id: dict[str, BundleArtifact] = {}
    for record in basis.native_bundles:
        bundle_id = record.bundle.completion_evidence_bundle_id
        if bundle_id not in bundle_rows or bundle_id in rendered_by_id:
            raise _inconsistent()
        rendered_by_id[bundle_id] = build_projection_bundle_artifact(record)
    if set(rendered_by_id) != set(bundle_rows):
        raise _inconsistent()

    entries: list[dict[str, Any]] = []
    used_bundle_ids: set[str] = set()
    for cycle in basis.cycles:
        if cycle.project_id != basis.project_id:
            raise _inconsistent()
        evidence_basis_version = _integer(cycle.evidence_basis_version)
        if evidence_basis_version == 0:
            if cycle.completion_evidence_bundle_id is not None:
                raise _inconsistent()
            entries.append(
                {
                    "task_id": cycle.task_id,
                    "completion_cycle_id": cycle.completion_cycle_id,
                    "cycle_ordinal": cycle.saved_cycle_ordinal,
                    "bundle_state": "legacy_unknown",
                    "bundle_id": None,
                    "bundle_file": None,
                    "bundle_digest": None,
                    "file_digest": None,
                    "sealed_at": None,
                    "bundle_format_version": None,
                }
            )
            continue
        bundle_id = cycle.completion_evidence_bundle_id
        if (
            evidence_basis_version != 1
            or type(bundle_id) is not str
            or bundle_id in used_bundle_ids
        ):
            raise _inconsistent()
        artifact = rendered_by_id.get(bundle_id)
        stored = bundle_rows.get(bundle_id)
        if (
            artifact is None
            or stored is None
            or stored.completion_cycle_id != cycle.completion_cycle_id
        ):
            raise _inconsistent()
        used_bundle_ids.add(bundle_id)
        entries.append(
            {
                "task_id": cycle.task_id,
                "completion_cycle_id": cycle.completion_cycle_id,
                "cycle_ordinal": cycle.saved_cycle_ordinal,
                "bundle_state": "native",
                "bundle_id": bundle_id,
                "bundle_file": f"bundles/{bundle_id}.json",
                "bundle_digest": artifact.bundle_digest,
                "file_digest": artifact.file_digest,
                "sealed_at": stored.sealed_at,
                "bundle_format_version": stored.bundle_version,
            }
        )
    if used_bundle_ids != set(rendered_by_id):
        raise _inconsistent()

    index = build_index_artifact(
        {
            "source_schema_version": basis.source_schema_version,
            "project_id": basis.project_id,
            "projection_generation": basis.source_generation,
            "bundle_count": len(rendered_by_id),
            "legacy_count": len(entries) - len(rendered_by_id),
            "entries": entries,
        }
    )
    return _RenderedProjection(
        source_generation=basis.source_generation,
        bundles=tuple(sorted(rendered_by_id.items())),
        index=index,
    )


def _capture(
    target: DatabaseTarget,
    *,
    include_basis: bool,
) -> _ProjectionCapture:
    project_id = target.project.project_id
    with closing(connect_initialized_readonly(target)) as connection:
        maintenance = read_project_maintenance(connection, project_id)
        state = read_evidence_projection_state(
            connection,
            project_id=project_id,
        )
        if maintenance is None:
            raise _inconsistent()
        basis = (
            capture_evidence_projection_basis(
                connection,
                project_id=project_id,
            )
            if include_basis
            else None
        )
        if basis is not None and (
            basis.project_id != project_id
            or basis.source_generation != state.source_generation
        ):
            raise _inconsistent()
        return _ProjectionCapture(
            maintenance=maintenance,
            state=state,
            basis=basis,
        )


def _fixed_output_paths(
    target: DatabaseTarget,
) -> tuple[Path, Path, Path, Path, Path]:
    state_root = target.db_path.parent
    evidence_root = target.resolved_evidence_root
    index_path = target.resolved_evidence_index
    bundles_path = target.resolved_evidence_bundles
    lock_path = target.resolved_evidence_lock
    if (
        evidence_root != state_root / "evidence"
        or index_path != evidence_root / "index.json"
        or bundles_path != evidence_root / "bundles"
        or lock_path != evidence_root / "taskgov-evidence.lock"
    ):
        raise StatePathError()
    for path in (evidence_root, index_path, bundles_path, lock_path):
        require_contained(path, state_root)
    return state_root, evidence_root, index_path, bundles_path, lock_path


def _prepare_output_directories(target: DatabaseTarget) -> None:
    state_root, evidence_root, _, bundles_path, _ = _fixed_output_paths(target)
    try:
        evidence_root.mkdir(exist_ok=True)
        inspect_physical_directory(evidence_root, root=state_root)
        bundles_path.mkdir(exist_ok=True)
        inspect_physical_directory(bundles_path, root=evidence_root)
    except StatePathError:
        raise
    except OSError as exc:
        raise StatePathError() from exc


def _temporary_file(
    directory: Path,
    *,
    root: Path,
    prefix: str,
    data: bytes,
    maximum: int,
) -> ValidatedFile:
    name = f"{prefix}{secrets.token_hex(4)}{_TEMP_SUFFIX}"
    return create_exclusive_durable_file(
        directory / name,
        data,
        root=root,
        max_bytes=maximum,
    )


def _discard_temporary(file: ValidatedFile | None, *, root: Path) -> None:
    if file is None or not path_lexically_exists(file.path):
        return
    with suppress(StatePathError):
        unlink_validated_file(file, root=root)


def _matches_document(
    path: Path,
    expected: bytes,
    *,
    root: Path,
    maximum: int,
) -> bool:
    if not path_lexically_exists(path):
        return False
    observed, _ = read_physical_file_bounded(
        path,
        root=root,
        max_bytes=maximum,
    )
    return observed == expected


def _same_file_object(left: FileIdentity, right: FileIdentity) -> bool:
    return (left.device, left.inode) == (right.device, right.inode)


def _observe_replace_destination(
    target: DatabaseTarget,
    destination: Path,
    *,
    root: Path,
    maximum: int,
    allow_unbounded: bool,
) -> tuple[bytes | None, FileIdentity | None]:
    if not path_lexically_exists(destination):
        return None, None
    if allow_unbounded:
        _, identity = inspect_physical_file(
            destination,
            root=root,
        )
        observed = None
    else:
        observed, validated = read_physical_file_bounded(
            destination,
            root=root,
            max_bytes=maximum,
        )
        identity = validated.identity
    _, database_identity = inspect_physical_file(
        target.db_path,
        root=target.db_path.parent,
    )
    if _same_file_object(identity, database_identity):
        raise StatePathError()
    return observed, identity


def _revalidate_replace_destination(
    destination: Path,
    *,
    root: Path,
    expected: FileIdentity | None,
) -> None:
    if expected is None:
        if path_lexically_exists(destination):
            raise StatePathError()
        return
    if not path_lexically_exists(destination):
        raise StatePathError()
    _, current = inspect_physical_file(destination, root=root)
    if current != expected:
        raise StatePathError()


def _publish_immutable_bundle(
    target: DatabaseTarget,
    bundle_id: str,
    artifact: BundleArtifact,
    *,
    replace_existing: bool = False,
) -> bool:
    _, evidence_root, _, bundles_path, _ = _fixed_output_paths(target)
    destination = bundles_path / f"{bundle_id}.json"
    require_contained(destination, evidence_root)
    observed, destination_identity = _observe_replace_destination(
        target,
        destination,
        root=evidence_root,
        maximum=BUNDLE_MAX_BYTES,
        allow_unbounded=replace_existing,
    )
    if destination_identity is not None:
        if not replace_existing:
            if observed == artifact.document:
                return False
            raise StatePathError()

    temporary = _temporary_file(
        bundles_path,
        root=evidence_root,
        prefix=_BUNDLE_TEMP_PREFIX,
        data=artifact.document,
        maximum=BUNDLE_MAX_BYTES,
    )
    try:
        if destination_identity is not None:
            _atomic_replace_temporary(
                temporary,
                destination,
                root=evidence_root,
                maximum=BUNDLE_MAX_BYTES,
                expected_destination=destination_identity,
            )
            temporary = None
            return True
        try:
            rename_no_replace(
                temporary,
                destination,
                root=evidence_root,
            )
        except StatePathError:
            if not replace_existing and _matches_document(
                destination,
                artifact.document,
                root=evidence_root,
                maximum=BUNDLE_MAX_BYTES,
            ):
                return False
            raise
        temporary = None
        return True
    finally:
        _discard_temporary(temporary, root=evidence_root)


def _atomic_replace_temporary(
    temporary: ValidatedFile,
    destination: Path,
    *,
    root: Path,
    maximum: int,
    expected_destination: FileIdentity | None,
) -> None:
    require_contained(destination, root)
    if temporary.path.parent != destination.parent:
        raise StatePathError()
    current_temp, identity = inspect_physical_file(
        temporary.path,
        root=root,
        max_bytes=maximum,
    )
    if identity != temporary.identity:
        raise StatePathError()
    if (
        expected_destination is not None
        and _same_file_object(identity, expected_destination)
    ):
        raise StatePathError()
    _revalidate_replace_destination(
        destination,
        root=root,
        expected=expected_destination,
    )
    parent_before = inspect_physical_directory(
        destination.parent,
        root=root,
    )
    _revalidate_replace_destination(
        destination,
        root=root,
        expected=expected_destination,
    )
    try:
        os.replace(temporary.path, destination)
    except OSError as exc:
        raise StatePathError() from exc
    parent_after = inspect_physical_directory(
        destination.parent,
        root=root,
    )
    published, published_identity = inspect_physical_file(
        destination,
        root=root,
        max_bytes=maximum,
    )
    if (
        current_temp != temporary.path
        or parent_before != parent_after
        or published != destination
        or published_identity != temporary.identity
    ):
        raise StatePathError()


@contextmanager
def _generation_guard(
    target: DatabaseTarget,
    *,
    captured_generation: int,
):
    project_id = target.project.project_id
    with closing(connect_initialized_readonly(target)) as connection:
        maintenance = read_project_maintenance(connection, project_id)
        state = read_evidence_projection_state(
            connection,
            project_id=project_id,
        )
        if maintenance is None or not maintenance.enabled:
            raise _EvidenceOptedOut()
        if state.source_generation != captured_generation:
            raise _EvidenceSourceChanged()
        yield


def _replace_index(
    target: DatabaseTarget,
    artifact: IndexArtifact,
    *,
    captured_generation: int,
    replace_existing_unbounded: bool = False,
) -> None:
    state_root, evidence_root, index_path, _, _ = _fixed_output_paths(target)
    _, destination_identity = _observe_replace_destination(
        target,
        index_path,
        root=state_root,
        maximum=INDEX_MAX_BYTES,
        allow_unbounded=replace_existing_unbounded,
    )
    temporary = _temporary_file(
        evidence_root,
        root=state_root,
        prefix=_INDEX_TEMP_PREFIX,
        data=artifact.document,
        maximum=INDEX_MAX_BYTES,
    )
    try:
        with _generation_guard(
            target,
            captured_generation=captured_generation,
        ):
            _atomic_replace_temporary(
                temporary,
                index_path,
                root=state_root,
                maximum=INDEX_MAX_BYTES,
                expected_destination=destination_identity,
            )
        temporary = None
    finally:
        _discard_temporary(temporary, root=state_root)


def _record_failure(
    target: DatabaseTarget,
    *,
    generation: int,
    code: str,
    occurred_at: str,
) -> None:
    with suppress(Exception):
        record_evidence_projection_outcome(
            target,
            captured_generation=generation,
            outcome_code=code,
            recorded_at=occurred_at,
        )


def _publish_evidence_projection(
    target: DatabaseTarget,
    *,
    force: bool,
    observed_at: str,
) -> EvidenceProjectionRefreshResult:
    observed_at = validate_utc_timestamp(
        observed_at,
        field="Evidence projection observation time",
    )
    publications = 0
    captured_generation = 0
    try:
        first = _capture(target, include_basis=False)
        captured_generation = first.state.source_generation
        if not first.maintenance.enabled:
            return EvidenceProjectionRefreshResult("not_opted_in", 0)
        if not force and not first.state.due:
            return EvidenceProjectionRefreshResult("current", 0)

        _prepare_output_directories(target)
        _, _, _, _, lock_path = _fixed_output_paths(target)
        with zero_wait_artifact_lock(lock_path):
            capture = _capture(target, include_basis=True)
            for attempt in range(MAX_PUBLICATIONS_PER_ATTEMPT):
                captured_generation = capture.state.source_generation
                if not capture.maintenance.enabled:
                    return EvidenceProjectionRefreshResult(
                        "not_opted_in",
                        publications,
                    )
                if (
                    not force
                    and not capture.state.due
                    and publications == 0
                ):
                    return EvidenceProjectionRefreshResult("current", 0)
                if capture.basis is None:
                    raise _inconsistent()
                rendered = _render_projection(capture.basis)
                for bundle_id, artifact in rendered.bundles:
                    _publish_immutable_bundle(
                        target,
                        bundle_id,
                        artifact,
                        replace_existing=force,
                    )
                try:
                    _replace_index(
                        target,
                        rendered.index,
                        captured_generation=rendered.source_generation,
                        replace_existing_unbounded=force,
                    )
                except _EvidenceSourceChanged:
                    if attempt + 1 >= MAX_PUBLICATIONS_PER_ATTEMPT:
                        raise RuntimeError(
                            "Evidence source changed during publication"
                        )
                    capture = _capture(target, include_basis=True)
                    continue
                except _EvidenceOptedOut:
                    return EvidenceProjectionRefreshResult(
                        "not_opted_in",
                        publications,
                    )

                state = record_evidence_projection_outcome(
                    target,
                    captured_generation=rendered.source_generation,
                    outcome_code="succeeded",
                    recorded_at=utc_now(),
                    index_digest=rendered.index.index_digest,
                )
                publications += 1
                if state.source_generation == rendered.source_generation:
                    return EvidenceProjectionRefreshResult(
                        "succeeded",
                        publications,
                    )
                if attempt + 1 >= MAX_PUBLICATIONS_PER_ATTEMPT:
                    raise RuntimeError(
                        "Evidence source changed during publication"
                    )
                capture = _capture(target, include_basis=True)
    except ArtifactLockError as exc:
        code = "deferred" if exc.contended else "failed"
        _record_failure(
            target,
            generation=captured_generation,
            code=code,
            occurred_at=observed_at,
        )
        return EvidenceProjectionRefreshResult(code, publications)
    except Exception:
        _record_failure(
            target,
            generation=captured_generation,
            code="failed",
            occurred_at=observed_at,
        )
        return EvidenceProjectionRefreshResult("failed", publications)
    return EvidenceProjectionRefreshResult("succeeded", publications)


def run_routine_evidence_projection(
    target: DatabaseTarget,
    *,
    observed_at: str | None = None,
) -> EvidenceProjectionRefreshResult:
    """Publish one due generation without waiting or changing its mutation."""

    return _publish_evidence_projection(
        target,
        force=False,
        observed_at=observed_at or utc_now(),
    )


def publish_setup_evidence_projection(
    target: DatabaseTarget,
    *,
    observed_at: str | None = None,
) -> EvidenceProjectionRefreshResult:
    """Force one bounded canonical publication for explicit setup repair."""

    return _publish_evidence_projection(
        target,
        force=True,
        observed_at=observed_at or utc_now(),
    )


def inspect_canonical_evidence_status(target: DatabaseTarget) -> str:
    """Return the read-only status of the fixed DB-derived Evidence files."""

    capture = _capture(target, include_basis=True)
    if capture.basis is None:
        raise _inconsistent()
    rendered = _render_projection(capture.basis)
    state_root, evidence_root, index_path, bundles_path, _ = (
        _fixed_output_paths(target)
    )
    if not path_lexically_exists(evidence_root):
        return "not_present"
    try:
        inspect_physical_directory(evidence_root, root=state_root)
        if not path_lexically_exists(index_path):
            return "not_present"
        inspect_physical_directory(bundles_path, root=evidence_root)
        if not _matches_document(
            index_path,
            rendered.index.document,
            root=state_root,
            maximum=INDEX_MAX_BYTES,
        ):
            return "repair_required"
        for bundle_id, artifact in rendered.bundles:
            if not _matches_document(
                bundles_path / f"{bundle_id}.json",
                artifact.document,
                root=evidence_root,
                maximum=BUNDLE_MAX_BYTES,
            ):
                return "repair_required"
    except (OSError, StatePathError, EvidenceProjectionError):
        return "repair_required"
    return (
        "current"
        if (
            not capture.state.due
            and capture.state.published_generation
            == rendered.source_generation
            and capture.state.index_digest == rendered.index.index_digest
        )
        else "repair_required"
    )
