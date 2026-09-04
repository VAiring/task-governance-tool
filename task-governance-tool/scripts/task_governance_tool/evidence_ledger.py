"""Pure schema-v18 evidence-ledger values and canonical digests.

This module deliberately has no SQLite dependency.  Repositories own IDs,
timestamps, uniqueness, and transaction boundaries; the functions here own
closed enums, immutable value matrices, and byte-exact digest inputs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import InitVar, dataclass, field
from typing import Any, Mapping


AUTHORITY_SNAPSHOT_DOMAIN = b"taskgov-authority-snapshot-v1\0"
CONTRACT_CRITERION_DOMAIN = b"taskgov-contract-criterion-v1\0"
EVIDENCE_REFERENCE_DOMAIN = b"taskgov-evidence-reference-v1\0"
VERIFICATION_EXPECTATION_DIGEST_DOMAIN = (
    b"taskgov-verification-expectation-v1\0"
)

AUTHORITY_SNAPSHOT_BASIS_FIELDS = frozenset(
    {
        "project_id",
        "task_id",
        "task_title",
        "task_description",
        "review_tier",
        "verification",
        "verification_digest",
        "contract_revision",
        "contract_state",
        "contract_scope",
        "contract_acceptance",
        "contract_constraints",
        "contract_authority_ref",
        "acceptance_criterion_id",
        "verification_criterion_id",
        "producer_class",
        "producer_version",
    }
)
CRITERION_TEXT_LIMITS = {
    "acceptance": 4_000,
    "verification": 1_000,
}

ASSURANCE_CLASSES = frozenset(
    {
        "machine_observed",
        "bound_attestation",
        "deterministically_derived",
        "external_reference",
        "legacy_unknown",
    }
)
PRODUCER_CLASSES = frozenset(
    {
        "taskgov_core",
        "taskgov_git",
        "trusted_caller",
        "legacy_migration",
        "external_system",
        "verification_runner",
    }
)
TARGET_KINDS = frozenset(
    {"git_commit", "diff_fingerprint", "external_revision", "git_snapshot"}
)
COMPLETION_EVIDENCE_KINDS = frozenset(
    {"git_commit", "external_revision", "commit_not_required"}
)
CRITERION_KINDS = frozenset({"acceptance", "verification"})
CONTRACT_STATES = frozenset({"contract_specified", "contract_unspecified"})

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_STABLE_ID = re.compile(r"tg_[a-z0-9_]+_[0-9a-f]{16}\Z")
_SNAPSHOT_ID = re.compile(r"tg_authority_snapshot_[0-9a-f]{16}\Z")
_CRITERION_ID = re.compile(r"tg_contract_criterion_[0-9a-f]{16}\Z")
_SOURCE_ID_PATTERNS = {
    "artifact_manifest": re.compile(r"tg_artifact_manifest_[0-9a-f]{16}\Z"),
    "verification_receipt": re.compile(r"tg_verification_receipt_[0-9a-f]{16}\Z"),
    "review_receipt": re.compile(r"tg_review_receipt_[0-9a-f]{16}\Z"),
    "review_finding": re.compile(r"tg_review_finding_[0-9a-f]{16}\Z"),
    "completion_evidence": re.compile(r"tg_completion_cycle_[0-9a-f]{16}\Z"),
    "runner_observation": re.compile(
        r"tg_verification_runner_observation_[0-9a-f]{16}\Z"
    ),
}

EVIDENCE_LEDGER_ERROR_CODE = "evidence_ledger_inconsistent"
EVIDENCE_LEDGER_ERROR_MESSAGE = "stored evidence ledger is inconsistent"
EVIDENCE_BASIS_STALE_CODE = "evidence_basis_stale"
EVIDENCE_BASIS_STALE_MESSAGE = "current evidence basis must be captured again"


@dataclass(frozen=True)
class EvidenceLedgerError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _inconsistent() -> EvidenceLedgerError:
    return EvidenceLedgerError(
        EVIDENCE_LEDGER_ERROR_CODE,
        EVIDENCE_LEDGER_ERROR_MESSAGE,
    )


def require_capture_v1(capture_version: object) -> None:
    """Reject source creation against retained capture-version-zero lineage."""

    if type(capture_version) is not int or capture_version != 1:
        raise EvidenceLedgerError(
            EVIDENCE_BASIS_STALE_CODE,
            EVIDENCE_BASIS_STALE_MESSAGE,
        )


def _validate_json_value(value: Any) -> None:
    if value is None or type(value) in {bool, int}:
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise _inconsistent() from exc
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _inconsistent()
            _validate_json_value(key)
            _validate_json_value(item)
        return
    raise _inconsistent()


def canonical_json_bytes(value: Any) -> bytes:
    """Encode the integer-only, sorted-key, compact UTF-8 v1 JSON form."""

    _validate_json_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _inconsistent() from exc


def domain_digest(domain: bytes, canonical_value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        domain + canonical_json_bytes(canonical_value)
    ).hexdigest()


def _text(value: object, *, maximum: int, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise _inconsistent()
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise _inconsistent() from exc
    if required and (not value or value != value.strip()):
        raise _inconsistent()
    return value


def _identifier(value: object, pattern: re.Pattern[str] | None = None) -> str:
    text = _text(value, maximum=200, required=True)
    if pattern is not None and pattern.fullmatch(text) is None:
        raise _inconsistent()
    return text


def _nullable_identifier(value: object) -> str | None:
    if value is None:
        return None
    return _identifier(value, _CRITERION_ID)


def verification_expectation_digest(exact_text: str) -> str:
    """Digest exact stored verification bytes using the schema-v17+ domain."""

    text = _text(exact_text, maximum=1_000)
    return hashlib.sha256(
        VERIFICATION_EXPECTATION_DIGEST_DOMAIN + text.encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class AuthorityBasis:
    task_title: str
    task_description: str
    review_tier: int
    verification: str
    contract_revision: int
    contract_state: str
    contract_scope: str
    contract_acceptance: str
    contract_constraints: str
    contract_authority_ref: str

    def __post_init__(self) -> None:
        _text(self.task_title, maximum=200, required=True)
        _text(self.task_description, maximum=4_000)
        if type(self.review_tier) is not int or self.review_tier not in {0, 1, 2}:
            raise _inconsistent()
        _text(self.verification, maximum=1_000)
        if type(self.contract_revision) is not int or self.contract_revision < 0:
            raise _inconsistent()
        if (
            not isinstance(self.contract_state, str)
            or self.contract_state not in CONTRACT_STATES
        ):
            raise _inconsistent()
        _text(self.contract_scope, maximum=4_000)
        _text(self.contract_acceptance, maximum=4_000)
        _text(self.contract_constraints, maximum=2_000)
        _text(self.contract_authority_ref, maximum=500)
        if self.contract_revision == 0:
            if self.contract_state != "contract_unspecified" or any(
                (
                    self.contract_scope,
                    self.contract_acceptance,
                    self.contract_constraints,
                    self.contract_authority_ref,
                )
            ):
                raise _inconsistent()
        elif self.contract_state != "contract_specified":
            raise _inconsistent()

    def canonical_value(self) -> dict[str, Any]:
        return {
            "contract_acceptance": self.contract_acceptance,
            "contract_authority_ref": self.contract_authority_ref,
            "contract_constraints": self.contract_constraints,
            "contract_revision": self.contract_revision,
            "contract_scope": self.contract_scope,
            "contract_state": self.contract_state,
            "review_tier": self.review_tier,
            "task_description": self.task_description,
            "task_title": self.task_title,
            "verification": self.verification,
        }


def _validated_authority_snapshot_basis_values(
    values: Mapping[str, Any],
) -> dict[str, Any]:
    if (
        not isinstance(values, Mapping)
        or frozenset(values) != AUTHORITY_SNAPSHOT_BASIS_FIELDS
    ):
        raise _inconsistent()
    exact = dict(values)
    try:
        basis = AuthorityBasis(
            task_title=exact["task_title"],
            task_description=exact["task_description"],
            review_tier=exact["review_tier"],
            verification=exact["verification"],
            contract_revision=exact["contract_revision"],
            contract_state=exact["contract_state"],
            contract_scope=exact["contract_scope"],
            contract_acceptance=exact["contract_acceptance"],
            contract_constraints=exact["contract_constraints"],
            contract_authority_ref=exact["contract_authority_ref"],
        )
        _identifier(exact["project_id"])
        _identifier(exact["task_id"])
        acceptance_id = _nullable_identifier(exact["acceptance_criterion_id"])
        verification_id = _nullable_identifier(
            exact["verification_criterion_id"]
        )
    except (KeyError, TypeError) as exc:
        raise _inconsistent() from exc
    if (
        exact["verification_digest"]
        != verification_expectation_digest(basis.verification)
        or exact["acceptance_criterion_id"] != acceptance_id
        or exact["verification_criterion_id"] != verification_id
        or (basis.contract_revision == 0) != (acceptance_id is None)
        or bool(basis.verification.strip())
        != (verification_id is not None)
        or not isinstance(exact["producer_class"], str)
        or exact["producer_class"] not in {"taskgov_core", "legacy_migration"}
        or type(exact["producer_version"]) is not int
        or exact["producer_version"] != 1
    ):
        raise _inconsistent()
    return exact


def authority_snapshot_basis_digest(values: Mapping[str, Any]) -> str:
    """Digest the exact schema-v18 17-field authority basis."""

    return domain_digest(
        AUTHORITY_SNAPSHOT_DOMAIN,
        _validated_authority_snapshot_basis_values(values),
    )


def _authority_snapshot_basis_values(
    *,
    project_id: str,
    task_id: str,
    basis: AuthorityBasis,
    verification_digest: str,
    acceptance_criterion_id: str | None,
    verification_criterion_id: str | None,
    producer_class: str,
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "task_id": task_id,
        **basis.canonical_value(),
        "verification_digest": verification_digest,
        "acceptance_criterion_id": acceptance_criterion_id,
        "verification_criterion_id": verification_criterion_id,
        "producer_class": producer_class,
        "producer_version": 1,
    }


@dataclass(frozen=True)
class AuthoritySnapshotSpec:
    authority_snapshot_id: str
    generation: int
    project_id: str
    task_id: str
    basis: AuthorityBasis
    verification_digest: str
    acceptance_criterion_id: str | None
    verification_criterion_id: str | None
    basis_digest: str
    producer_class: str
    producer_version: int = 1

    def __post_init__(self) -> None:
        _identifier(self.authority_snapshot_id, _SNAPSHOT_ID)
        if type(self.generation) is not int or self.generation <= 0:
            raise _inconsistent()
        if not isinstance(self.basis, AuthorityBasis):
            raise _inconsistent()
        expected_verification = verification_expectation_digest(
            self.basis.verification
        )
        if self.verification_digest != expected_verification:
            raise _inconsistent()
        if (
            not isinstance(self.producer_class, str)
            or self.producer_class
            not in {"taskgov_core", "legacy_migration"}
        ):
            raise _inconsistent()
        if type(self.producer_version) is not int or self.producer_version != 1:
            raise _inconsistent()
        digest_values = _authority_snapshot_basis_values(
            project_id=self.project_id,
            task_id=self.task_id,
            basis=self.basis,
            verification_digest=self.verification_digest,
            acceptance_criterion_id=self.acceptance_criterion_id,
            verification_criterion_id=self.verification_criterion_id,
            producer_class=self.producer_class,
        )
        if self.basis_digest != authority_snapshot_basis_digest(digest_values):
            raise _inconsistent()


def build_authority_snapshot(
    *,
    authority_snapshot_id: str,
    generation: int,
    project_id: str,
    task_id: str,
    basis: AuthorityBasis,
    acceptance_criterion_id: str | None,
    verification_criterion_id: str | None,
    producer_class: str = "taskgov_core",
) -> AuthoritySnapshotSpec:
    if not isinstance(basis, AuthorityBasis):
        raise _inconsistent()
    verification_digest = verification_expectation_digest(basis.verification)
    digest_values = _authority_snapshot_basis_values(
        project_id=project_id,
        task_id=task_id,
        basis=basis,
        verification_digest=verification_digest,
        acceptance_criterion_id=acceptance_criterion_id,
        verification_criterion_id=verification_criterion_id,
        producer_class=producer_class,
    )
    return AuthoritySnapshotSpec(
        authority_snapshot_id=authority_snapshot_id,
        generation=generation,
        project_id=project_id,
        task_id=task_id,
        basis=basis,
        verification_digest=verification_digest,
        acceptance_criterion_id=acceptance_criterion_id,
        verification_criterion_id=verification_criterion_id,
        basis_digest=authority_snapshot_basis_digest(digest_values),
        producer_class=producer_class,
    )


@dataclass(frozen=True)
class ContractCriterionSpec:
    criterion_id: str
    criterion_kind: str
    criterion_text: str
    digest: str

    def __post_init__(self) -> None:
        _identifier(self.criterion_id, _CRITERION_ID)
        if (
            not isinstance(self.criterion_kind, str)
            or self.criterion_kind not in CRITERION_KINDS
        ):
            raise _inconsistent()
        if self.digest != contract_criterion_digest(
            self.criterion_kind,
            self.criterion_text,
        ):
            raise _inconsistent()


def contract_criterion_digest(criterion_kind: str, criterion_text: str) -> str:
    if (
        not isinstance(criterion_kind, str)
        or criterion_kind not in CRITERION_KINDS
    ):
        raise _inconsistent()
    text = _text(
        criterion_text,
        maximum=CRITERION_TEXT_LIMITS[criterion_kind],
    )
    if not text.strip():
        raise _inconsistent()
    payload = (
        CONTRACT_CRITERION_DOMAIN
        + criterion_kind.encode("utf-8")
        + b"\0"
        + text.encode("utf-8")
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def build_contract_criterion(
    *,
    criterion_id: str,
    criterion_kind: str,
    criterion_text: str,
) -> ContractCriterionSpec:
    return ContractCriterionSpec(
        criterion_id=criterion_id,
        criterion_kind=criterion_kind,
        criterion_text=criterion_text,
        digest=contract_criterion_digest(criterion_kind, criterion_text),
    )


def criteria_for_authority_basis(
    basis: AuthorityBasis,
    *,
    acceptance_criterion_id: str | None,
    verification_criterion_id: str | None,
) -> tuple[ContractCriterionSpec | None, ContractCriterionSpec | None]:
    """Build the at-most-one whole-field criteria without rewriting text."""

    if not isinstance(basis, AuthorityBasis):
        raise _inconsistent()
    if basis.contract_revision == 0:
        if acceptance_criterion_id is not None:
            raise _inconsistent()
        acceptance = None
    else:
        if not basis.contract_acceptance.strip():
            if acceptance_criterion_id is not None:
                raise _inconsistent()
            acceptance = None
        else:
            if acceptance_criterion_id is None:
                raise _inconsistent()
            acceptance = build_contract_criterion(
                criterion_id=acceptance_criterion_id,
                criterion_kind="acceptance",
                criterion_text=basis.contract_acceptance,
            )
    if not basis.verification.strip():
        if verification_criterion_id is not None:
            raise _inconsistent()
        verification = None
    else:
        if verification_criterion_id is None:
            raise _inconsistent()
        verification = build_contract_criterion(
            criterion_id=verification_criterion_id,
            criterion_kind="verification",
            criterion_text=basis.verification,
        )
    return acceptance, verification


@dataclass(frozen=True)
class TargetCaptureBinding:
    target_kind: str
    target_value: str
    target_base_revision: str
    target_generation: int
    authority_snapshot_id: str
    acceptance_criterion_id: str | None
    verification_criterion_id: str | None
    capture_version: int = 1

    def __post_init__(self) -> None:
        if self.target_kind not in TARGET_KINDS:
            raise _inconsistent()
        _text(self.target_value, maximum=500, required=True)
        _text(self.target_base_revision, maximum=500)
        if type(self.target_generation) is not int or self.target_generation <= 0:
            raise _inconsistent()
        _identifier(self.authority_snapshot_id, _SNAPSHOT_ID)
        _nullable_identifier(self.acceptance_criterion_id)
        _nullable_identifier(self.verification_criterion_id)
        if type(self.capture_version) is not int or self.capture_version not in {0, 1}:
            raise _inconsistent()
        if self.target_kind == "git_snapshot":
            if (
                _DIGEST.fullmatch(self.target_value) is None
                or _GIT_OBJECT_ID.fullmatch(self.target_base_revision) is None
                or set(self.target_base_revision) == {"0"}
            ):
                raise _inconsistent()
        elif self.target_base_revision:
            raise _inconsistent()
        elif self.target_kind == "git_commit" and (
            _GIT_OBJECT_ID.fullmatch(self.target_value) is None
            or set(self.target_value) == {"0"}
        ):
            raise _inconsistent()
        elif self.target_kind == "diff_fingerprint" and _DIGEST.fullmatch(
            self.target_value
        ) is None:
            raise _inconsistent()

    def canonical_value(self) -> dict[str, Any]:
        return {
            "acceptance_criterion_id": self.acceptance_criterion_id,
            "authority_snapshot_id": self.authority_snapshot_id,
            "target_base_revision": self.target_base_revision,
            "target_generation": self.target_generation,
            "target_kind": self.target_kind,
            "target_value": self.target_value,
            "verification_criterion_id": self.verification_criterion_id,
        }


@dataclass(frozen=True)
class ProducerAttribution:
    assurance_class: str
    producer_class: str
    producer_version: int = 1

    def __post_init__(self) -> None:
        if self.assurance_class not in ASSURANCE_CLASSES:
            raise _inconsistent()
        if self.producer_class not in PRODUCER_CLASSES:
            raise _inconsistent()
        if type(self.producer_version) is not int or self.producer_version <= 0:
            raise _inconsistent()


_VERIFICATION_PROJECTION = frozenset(
    {
        "verification_receipt_id",
        "subject_basis_version",
        "authority_snapshot_id",
        "verification_criterion_id",
        "result",
        "duration_ms",
        "scope_coverage",
        "created_at",
    }
)
_REVIEW_PROJECTION = frozenset(
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
_FINDING_PROJECTION = frozenset(
    {"review_finding_id", "review_receipt_id", "severity", "summary", "created_at"}
)
_COMPLETION_PROJECTION = frozenset(
    {
        "completion_cycle_id",
        "completed_at",
        "completion_evidence_kind",
        "completion_evidence_revision",
        "completion_evidence_reason",
        "external_revision_approved",
        "completion_commit_required",
        "completion_commit_hash",
    }
)
_RUNNER_OBSERVATION_PROJECTION = frozenset(
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
_COMPLETE_MANIFEST_PROJECTION = frozenset(
    {
        "artifact_manifest_id",
        "state",
        "object_format",
        "comparison_base",
        "entry_count",
        "digest",
        "omission_code",
    }
)
_OPAQUE_MANIFEST_PROJECTION = frozenset(
    {"artifact_manifest_id", "state", "target_kind", "digest", "omission_code"}
)
_REVIEW_PROVENANCE_KEYS = frozenset(
    {
        "review_provenance_id",
        "provenance_version",
        "reviewer_class",
        "model_state",
        "declared_model_id",
        "skill_state",
        "declared_skill_id",
        "declared_skill_version",
        "review_profiles",
        "review_lenses",
        "context_relation",
        "method_codes",
        "assurance_class",
        "producer_class",
        "producer_version",
        "digest",
    }
)


def _validate_review_provenance_projection(projection: dict[str, Any]) -> None:
    provenance = projection["review_provenance"]
    if projection["receipt_kind"] == "not_required":
        if provenance is not None:
            raise _inconsistent()
        return
    if projection["receipt_kind"] not in {"independent", "self_review_fallback"}:
        raise _inconsistent()
    if not isinstance(provenance, dict) or frozenset(provenance) != _REVIEW_PROVENANCE_KEYS:
        raise _inconsistent()
    if (
        provenance["provenance_version"] != 1
        or provenance["assurance_class"] != "bound_attestation"
        or provenance["producer_class"] != "trusted_caller"
        or provenance["producer_version"] != 1
        or not isinstance(provenance["digest"], str)
        or _DIGEST.fullmatch(provenance["digest"]) is None
    ):
        raise _inconsistent()
    _identifier(provenance["review_provenance_id"])
    for key in ("review_profiles", "review_lenses", "method_codes"):
        if not isinstance(provenance[key], list):
            raise _inconsistent()


@dataclass(frozen=True)
class EvidenceSource:
    source_kind: str
    source_state: str
    source_id: str
    source_projection: Mapping[str, Any]
    _validated_runner_eligibility_version: InitVar[int] = field(
        default=0,
        kw_only=True,
        repr=False,
    )

    def __post_init__(self, _validated_runner_eligibility_version: int) -> None:
        if (
            type(_validated_runner_eligibility_version) is not int
            or _validated_runner_eligibility_version not in {0, 1}
            or (
                _validated_runner_eligibility_version != 0
                and self.source_kind != "runner_observation"
            )
        ):
            raise _inconsistent()
        if not isinstance(self.source_projection, Mapping):
            raise _inconsistent()
        projection = dict(self.source_projection)
        _validate_json_value(projection)
        expected: frozenset[str]
        if self.source_kind == "artifact_manifest":
            if self.source_state == "complete_git":
                expected = _COMPLETE_MANIFEST_PROJECTION
            elif self.source_state == "opaque_target":
                expected = _OPAQUE_MANIFEST_PROJECTION
            else:
                raise _inconsistent()
        elif self.source_kind == "verification_receipt" and self.source_state == "recorded":
            expected = _VERIFICATION_PROJECTION
        elif self.source_kind == "review_receipt" and self.source_state == "recorded":
            expected = _REVIEW_PROJECTION
        elif self.source_kind == "review_finding" and self.source_state == "recorded":
            expected = _FINDING_PROJECTION
        elif (
            self.source_kind == "completion_evidence"
            and self.source_state in COMPLETION_EVIDENCE_KINDS
        ):
            expected = _COMPLETION_PROJECTION
        elif (
            self.source_kind == "runner_observation"
            and self.source_state == "recorded"
        ):
            expected = _RUNNER_OBSERVATION_PROJECTION
        else:
            raise _inconsistent()
        _identifier(self.source_id, _SOURCE_ID_PATTERNS[self.source_kind])
        if frozenset(projection) != expected:
            raise _inconsistent()
        if self.source_kind == "artifact_manifest":
            if projection["artifact_manifest_id"] != self.source_id:
                raise _inconsistent()
            if projection["state"] != self.source_state:
                raise _inconsistent()
            if not isinstance(projection["digest"], str) or _DIGEST.fullmatch(
                projection["digest"]
            ) is None:
                raise _inconsistent()
            if self.source_state == "complete_git":
                if (
                    projection["object_format"] not in {"sha1", "sha256"}
                    or not isinstance(projection["comparison_base"], str)
                    or _GIT_OBJECT_ID.fullmatch(projection["comparison_base"])
                    is None
                    or len(projection["comparison_base"])
                    != (40 if projection["object_format"] == "sha1" else 64)
                    or type(projection["entry_count"]) is not int
                    or not 0 <= projection["entry_count"] <= 10_000
                    or projection["omission_code"] is not None
                ):
                    raise _inconsistent()
            elif (
                projection["target_kind"] not in {"diff_fingerprint", "external_revision"}
                or projection["omission_code"] != "artifact_content_not_observed"
            ):
                raise _inconsistent()
        elif self.source_kind == "verification_receipt":
            if (
                projection["verification_receipt_id"] != self.source_id
                or projection["subject_basis_version"] != 1
                or projection["result"] not in {"pass", "fail", "timeout"}
                or type(projection["duration_ms"]) is not int
                or projection["duration_ms"] < 0
                or projection["scope_coverage"] not in {"full", "partial"}
            ):
                raise _inconsistent()
        elif self.source_kind == "review_receipt":
            if (
                projection["review_receipt_id"] != self.source_id
                or projection["receipt_kind"]
                not in {"independent", "self_review_fallback", "not_required"}
                or projection["verdict"]
                not in {"pass", "changes_requested", "not_required"}
                or type(projection["user_approved"]) is not int
                or projection["user_approved"] not in {0, 1}
            ):
                raise _inconsistent()
            _validate_review_provenance_projection(projection)
        elif self.source_kind == "review_finding":
            if (
                projection["review_finding_id"] != self.source_id
                or projection["severity"] not in {"high", "medium", "low"}
            ):
                raise _inconsistent()
        elif self.source_kind == "completion_evidence":
            if (
                projection["completion_cycle_id"] != self.source_id
                or projection["completion_evidence_kind"] != self.source_state
            ):
                raise _inconsistent()
        elif self.source_kind == "runner_observation":
            digest_fields = (
                "plan_raw_digest",
                "plan_semantic_digest",
                "runner_implementation_digest",
                "runner_policy_digest",
                "sanitized_result_digest",
            )
            if (
                projection["observation_id"] != self.source_id
                or type(projection["gate_eligibility_version"]) is not int
                or projection["gate_eligibility_version"]
                != _validated_runner_eligibility_version
                or projection["route"] not in {"runner", "m21_fallback"}
                or projection["launch_state"] not in {"no_launch", "launched"}
                or type(projection["complete_plan"]) is not int
                or projection["complete_plan"] not in {0, 1}
                or type(projection["total_step_count"]) is not int
                or not 1 <= projection["total_step_count"] <= 16
                or type(projection["completed_step_count"]) is not int
                or not 0
                <= projection["completed_step_count"]
                <= projection["total_step_count"]
                or projection["plan_blob_object_id"] is not None
                or type(projection["plan_version"]) is not int
                or projection["plan_version"] != 1
                or projection["runner_implementation_version"]
                != "taskgov-verification-runner/1"
                or projection["runtime_digest"] is not None
                or any(
                    type(projection[field]) is not str
                    or _DIGEST.fullmatch(projection[field]) is None
                    for field in digest_fields
                )
            ):
                raise _inconsistent()
        object.__setattr__(self, "source_projection", projection)


def evidence_source_attribution(
    source: EvidenceSource,
    *,
    target_kind: str,
) -> ProducerAttribution:
    """Return the non-caller-selectable source dispatch for schema v18."""

    if not isinstance(source, EvidenceSource) or target_kind not in TARGET_KINDS:
        raise _inconsistent()
    if source.source_kind == "artifact_manifest":
        if source.source_state == "complete_git" and target_kind in {
            "git_snapshot",
            "git_commit",
        }:
            return ProducerAttribution("machine_observed", "taskgov_git")
        if source.source_state == "opaque_target" and target_kind == "diff_fingerprint":
            return ProducerAttribution("bound_attestation", "trusted_caller")
        if source.source_state == "opaque_target" and target_kind == "external_revision":
            return ProducerAttribution("external_reference", "external_system")
    elif source.source_kind in {
        "verification_receipt",
        "review_receipt",
        "review_finding",
    }:
        return ProducerAttribution("bound_attestation", "trusted_caller")
    elif source.source_kind == "completion_evidence":
        if source.source_state == "git_commit":
            return ProducerAttribution("machine_observed", "taskgov_git")
        if source.source_state == "external_revision":
            return ProducerAttribution("external_reference", "external_system")
        if source.source_state == "commit_not_required":
            return ProducerAttribution("bound_attestation", "trusted_caller")
    elif source.source_kind == "runner_observation":
        return ProducerAttribution("machine_observed", "verification_runner")
    raise _inconsistent()


@dataclass(frozen=True)
class EvidenceReferenceSpec:
    source: EvidenceSource
    project_id: str
    task_id: str
    contract_revision: int
    binding: TargetCaptureBinding
    completion_cycle_id: str | None
    attribution: ProducerAttribution
    digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, EvidenceSource):
            raise _inconsistent()
        _identifier(self.project_id)
        _identifier(self.task_id)
        if type(self.contract_revision) is not int or self.contract_revision < 0:
            raise _inconsistent()
        if not isinstance(self.binding, TargetCaptureBinding):
            raise _inconsistent()
        require_capture_v1(self.binding.capture_version)
        expected_attribution = evidence_source_attribution(
            self.source,
            target_kind=self.binding.target_kind,
        )
        if self.attribution != expected_attribution:
            raise _inconsistent()
        if self.source.source_kind == "completion_evidence":
            if self.completion_cycle_id is None:
                raise _inconsistent()
            _identifier(self.completion_cycle_id, _STABLE_ID)
        elif self.completion_cycle_id is not None:
            raise _inconsistent()
        if self.digest != evidence_reference_digest(
            source=self.source,
            project_id=self.project_id,
            task_id=self.task_id,
            contract_revision=self.contract_revision,
            binding=self.binding,
            completion_cycle_id=self.completion_cycle_id,
            attribution=self.attribution,
        ):
            raise _inconsistent()


def _evidence_reference_value(
    *,
    source: EvidenceSource,
    project_id: str,
    task_id: str,
    contract_revision: int,
    binding: TargetCaptureBinding,
    completion_cycle_id: str | None,
    attribution: ProducerAttribution,
) -> dict[str, Any]:
    value = {
        "acceptance_criterion_id": binding.acceptance_criterion_id,
        "assurance_class": attribution.assurance_class,
        "authority_snapshot_id": binding.authority_snapshot_id,
        "completion_cycle_id": completion_cycle_id,
        "contract_revision": contract_revision,
        "producer_class": attribution.producer_class,
        "producer_version": attribution.producer_version,
        "project_id": project_id,
        "source_id": source.source_id,
        "source_kind": source.source_kind,
        "source_projection": dict(source.source_projection),
        "source_state": source.source_state,
        "target_base_revision": binding.target_base_revision,
        "target_generation": binding.target_generation,
        "target_kind": binding.target_kind,
        "target_value": binding.target_value,
        "task_id": task_id,
        "verification_criterion_id": binding.verification_criterion_id,
    }
    return value


def evidence_reference_digest(
    *,
    source: EvidenceSource,
    project_id: str,
    task_id: str,
    contract_revision: int,
    binding: TargetCaptureBinding,
    completion_cycle_id: str | None,
    attribution: ProducerAttribution | None = None,
) -> str:
    if not isinstance(source, EvidenceSource) or not isinstance(binding, TargetCaptureBinding):
        raise _inconsistent()
    require_capture_v1(binding.capture_version)
    _identifier(project_id)
    _identifier(task_id)
    if type(contract_revision) is not int or contract_revision < 0:
        raise _inconsistent()
    expected = evidence_source_attribution(source, target_kind=binding.target_kind)
    if attribution is not None and attribution != expected:
        raise _inconsistent()
    if source.source_kind == "completion_evidence":
        if completion_cycle_id is None:
            raise _inconsistent()
        _identifier(completion_cycle_id, _STABLE_ID)
        if completion_cycle_id != source.source_id:
            raise _inconsistent()
    elif completion_cycle_id is not None:
        raise _inconsistent()
    if source.source_kind == "artifact_manifest" and source.source_state == "opaque_target":
        if source.source_projection["target_kind"] != binding.target_kind:
            raise _inconsistent()
    if source.source_kind == "verification_receipt":
        if (
            binding.verification_criterion_id is None
            or source.source_projection["authority_snapshot_id"]
            != binding.authority_snapshot_id
            or source.source_projection["verification_criterion_id"]
            != binding.verification_criterion_id
        ):
            raise _inconsistent()
    return domain_digest(
        EVIDENCE_REFERENCE_DOMAIN,
        _evidence_reference_value(
            source=source,
            project_id=project_id,
            task_id=task_id,
            contract_revision=contract_revision,
            binding=binding,
            completion_cycle_id=completion_cycle_id,
            attribution=expected,
        ),
    )


def build_evidence_reference(
    *,
    source: EvidenceSource,
    project_id: str,
    task_id: str,
    contract_revision: int,
    binding: TargetCaptureBinding,
    completion_cycle_id: str | None = None,
) -> EvidenceReferenceSpec:
    attribution = evidence_source_attribution(source, target_kind=binding.target_kind)
    digest = evidence_reference_digest(
        source=source,
        project_id=project_id,
        task_id=task_id,
        contract_revision=contract_revision,
        binding=binding,
        completion_cycle_id=completion_cycle_id,
        attribution=attribution,
    )
    return EvidenceReferenceSpec(
        source=source,
        project_id=project_id,
        task_id=task_id,
        contract_revision=contract_revision,
        binding=binding,
        completion_cycle_id=completion_cycle_id,
        attribution=attribution,
        digest=digest,
    )
