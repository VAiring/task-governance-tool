"""Pure Review provenance normalization, projection, and validation."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

REVIEW_PROVENANCE_FIELDS = (
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
)
REVIEWER_CLASSES = (
    "human",
    "llm",
    "deterministic_tool",
    "hybrid",
    "unknown",
)
MODEL_STATES = ("declared", "not_applicable", "unknown")
SKILL_STATES = ("declared", "not_applicable", "not_used", "unknown")
CONTEXT_RELATIONS = (
    "same_context",
    "forked_context",
    "fresh_context",
    "external_context",
    "not_applicable",
    "unknown",
)
REVIEW_PROFILES = (
    "general",
    "authority_contract",
    "implementation",
    "verification",
    "migration_compatibility",
    "privacy_safety",
    "release_acceptance",
)
REVIEW_LENSES = (
    "correctness",
    "contract_compliance",
    "state_completion_integrity",
    "privacy",
    "target_safety",
    "verification_regression",
    "migration_compatibility",
    "maintainability",
    "accessibility",
    "performance",
    "release_integrity",
)
REVIEW_METHODS = (
    "review_packet_inspection",
    "authority_cross_check",
    "diff_inspection",
    "source_inspection",
    "test_inspection",
    "verification_evidence_inspection",
    "artifact_inspection",
    "runtime_observation",
    "deterministic_rule_check",
)

PROVENANCE_RECEIPT_KINDS = ("independent", "self_review_fallback")
ALL_RECEIPT_KINDS = (*PROVENANCE_RECEIPT_KINDS, "not_required")
TARGET_FIELDS = (
    "kind",
    "value",
    "base_revision",
    "generation",
    "capture_version",
)
V1_ASSURANCE_CLASS = "bound_attestation"
V1_PRODUCER_CLASS = "trusted_caller"
LEGACY_ASSURANCE_CLASS = "legacy_unknown"
LEGACY_PRODUCER_CLASS = "legacy_migration"
PRODUCER_VERSION = 1

REVIEW_PROVENANCE_ID_PATTERN = re.compile(
    r"^tg_review_provenance_[0-9a-f]{16}$"
)
DECLARED_IDENTIFIER_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}$"
)
DECLARED_SKILL_VERSION_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$"
)
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
DIGEST_DOMAIN = b"taskgov-review-provenance-v1\0"
INVALID_REVIEW_PROVENANCE_MESSAGE = "review provenance is invalid"


@dataclass(frozen=True)
class ReviewProvenanceError(Exception):
    """Sanitized semantic failure convertible to ReviewEvidenceError."""

    code: str = "invalid_review_evidence"
    message: str = INVALID_REVIEW_PROVENANCE_MESSAGE
    field: str | None = "review_provenance"

    def __str__(self) -> str:
        return self.message


def _invalid() -> ReviewProvenanceError:
    return ReviewProvenanceError()


def _has_exact_keys(value: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
    return len(value) == len(fields) and set(value) == set(fields)


def generate_review_provenance_id() -> str:
    return f"tg_review_provenance_{secrets.token_hex(8)}"


def _reject_private_or_raw_content(field: str, value: str) -> None:
    # The lazy import keeps this pure module safe for storage.py to import.
    from task_governance_tool.tasks import reject_private_or_raw_content

    reject_private_or_raw_content(field, value)


def _privacy_scan_value(field: str, value: Any) -> None:
    if isinstance(value, str):
        _reject_private_or_raw_content(field, value)
        return
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for item in value:
            if isinstance(item, str):
                _reject_private_or_raw_content(field, item)


def _normalized_choice(value: Any, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise _invalid()
    normalized = value.strip()
    if normalized not in allowed:
        raise _invalid()
    return normalized


def _optional_identifier(
    value: Any,
    *,
    pattern: re.Pattern[str],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _invalid()
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _invalid() from exc
    return value


def _normalized_codes(
    value: Any,
    *,
    allowed: tuple[str, ...],
    maximum: int,
) -> list[str]:
    if value is None:
        items: list[Any] = []
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        items = list(value)
    else:
        raise _invalid()
    if len(items) > maximum:
        raise _invalid()
    normalized: list[str] = []
    for item in items:
        normalized.append(_normalized_choice(item, allowed))
    if len(set(normalized)) != len(normalized):
        raise _invalid()
    selected = set(normalized)
    return [code for code in allowed if code in selected]


def normalize_review_provenance_input(
    *,
    receipt_kind: Any,
    reviewer_class: Any = None,
    model_state: Any = None,
    declared_model_id: Any = None,
    skill_state: Any = None,
    declared_skill_id: Any = None,
    declared_skill_version: Any = None,
    review_profiles: Any = None,
    review_lenses: Any = None,
    context_relation: Any = None,
    method_codes: Any = None,
) -> dict[str, Any] | None:
    """Normalize one caller-supplied provenance declaration.

    Privacy checks run across every supplied string before enum, bound,
    duplicate, grammar, kind, or cross-field validation. Privacy failures keep
    the existing ``TaskValidationError`` contract; all other failures use the
    sanitized ``ReviewProvenanceError`` boundary.
    """

    raw_values = {
        "receipt_kind": receipt_kind,
        "reviewer_class": reviewer_class,
        "model_state": model_state,
        "declared_model_id": declared_model_id,
        "skill_state": skill_state,
        "declared_skill_id": declared_skill_id,
        "declared_skill_version": declared_skill_version,
        "review_profiles": review_profiles,
        "review_lenses": review_lenses,
        "context_relation": context_relation,
        "method_codes": method_codes,
    }
    for field, value in raw_values.items():
        _privacy_scan_value(field, value)

    normalized_kind = _normalized_choice(receipt_kind, ALL_RECEIPT_KINDS)
    provenance_values = tuple(raw_values.values())[1:]
    if normalized_kind == "not_required":
        if any(value is not None and value != [] and value != () for value in provenance_values):
            raise _invalid()
        return None

    normalized_reviewer_class = _normalized_choice(
        reviewer_class, REVIEWER_CLASSES
    )
    normalized_model_state = _normalized_choice(model_state, MODEL_STATES)
    normalized_skill_state = _normalized_choice(skill_state, SKILL_STATES)
    normalized_context = _normalized_choice(context_relation, CONTEXT_RELATIONS)
    normalized_model_id = _optional_identifier(
        declared_model_id,
        pattern=DECLARED_IDENTIFIER_PATTERN,
    )
    normalized_skill_id = _optional_identifier(
        declared_skill_id,
        pattern=DECLARED_IDENTIFIER_PATTERN,
    )
    normalized_skill_version = _optional_identifier(
        declared_skill_version,
        pattern=DECLARED_SKILL_VERSION_PATTERN,
    )
    normalized_profiles = _normalized_codes(
        review_profiles,
        allowed=REVIEW_PROFILES,
        maximum=4,
    )
    normalized_lenses = _normalized_codes(
        review_lenses,
        allowed=REVIEW_LENSES,
        maximum=8,
    )
    normalized_methods = _normalized_codes(
        method_codes,
        allowed=REVIEW_METHODS,
        maximum=8,
    )

    model_declared = (
        normalized_model_state == "declared"
        and normalized_model_id is not None
    )
    model_unknown = (
        normalized_model_state == "unknown"
        and normalized_model_id is None
    )
    skill_declared = (
        normalized_skill_state == "declared"
        and normalized_skill_id is not None
        and normalized_skill_version is not None
    )
    skill_without_declaration = (
        normalized_skill_state in {"not_used", "unknown"}
        and normalized_skill_id is None
        and normalized_skill_version is None
    )

    if normalized_reviewer_class in {"human", "deterministic_tool"}:
        valid_matrix = (
            normalized_model_state == "not_applicable"
            and normalized_model_id is None
            and normalized_skill_state == "not_applicable"
            and normalized_skill_id is None
            and normalized_skill_version is None
        )
    elif normalized_reviewer_class == "llm":
        valid_matrix = (
            (model_declared or model_unknown)
            and (skill_declared or skill_without_declaration)
        )
    elif normalized_reviewer_class == "hybrid":
        valid_matrix = (
            (model_declared or model_unknown)
            and (skill_declared or skill_without_declaration)
        )
    else:
        valid_matrix = (
            normalized_model_state == "unknown"
            and normalized_model_id is None
            and normalized_skill_state == "unknown"
            and normalized_skill_id is None
            and normalized_skill_version is None
        )
    if not valid_matrix:
        raise _invalid()

    return {
        "reviewer_class": normalized_reviewer_class,
        "model_state": normalized_model_state,
        "declared_model_id": normalized_model_id,
        "skill_state": normalized_skill_state,
        "declared_skill_id": normalized_skill_id,
        "declared_skill_version": normalized_skill_version,
        "review_profiles": normalized_profiles,
        "review_lenses": normalized_lenses,
        "context_relation": normalized_context,
        "method_codes": normalized_methods,
    }


def _validate_context_value(field: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid() from exc
    _reject_private_or_raw_content(field, value)
    return value


def _normalize_target(target: Any) -> dict[str, Any]:
    if not isinstance(target, Mapping) or not _has_exact_keys(target, TARGET_FIELDS):
        raise _invalid()
    kind = _validate_context_value("target_kind", target["kind"])
    value = _validate_context_value("target_value", target["value"])
    base_revision = target["base_revision"]
    generation = target["generation"]
    capture_version = target["capture_version"]
    if (
        not isinstance(base_revision, str)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation <= 0
        or isinstance(capture_version, bool)
        or not isinstance(capture_version, int)
        or capture_version != 1
    ):
        raise _invalid()
    try:
        base_revision.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _invalid() from exc
    _reject_private_or_raw_content("target_base_revision", base_revision)
    return {
        "kind": kind,
        "value": value,
        "base_revision": base_revision,
        "generation": generation,
        "capture_version": capture_version,
    }


def _validate_canonical_value(value: Any) -> None:
    if value is None or isinstance(value, (bool, int)) and not isinstance(value, float):
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _invalid() from exc
        return
    if isinstance(value, list):
        for item in value:
            _validate_canonical_value(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise _invalid()
        for key, item in value.items():
            _validate_canonical_value(key)
            _validate_canonical_value(item)
        return
    raise _invalid()


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    _validate_canonical_value(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _invalid() from exc


def _digest_payload(
    *,
    project_id: str,
    task_id: str,
    review_receipt_id: str,
    receipt_kind: str,
    target: dict[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "task_id": task_id,
        "review_receipt_id": review_receipt_id,
        "receipt_kind": receipt_kind,
        "target": target,
        **{
            field: provenance[field]
            for field in REVIEW_PROVENANCE_FIELDS[1:-1]
        },
    }


def review_provenance_digest(
    *,
    project_id: Any,
    task_id: Any,
    review_receipt_id: Any,
    receipt_kind: Any,
    target: Any,
    provenance: Mapping[str, Any],
) -> str:
    if not isinstance(provenance, Mapping) or not _has_exact_keys(
        provenance, REVIEW_PROVENANCE_FIELDS
    ):
        raise _invalid()
    normalized_project_id = _validate_context_value("project_id", project_id)
    normalized_task_id = _validate_context_value("task_id", task_id)
    normalized_receipt_id = _validate_context_value(
        "review_receipt_id", review_receipt_id
    )
    normalized_kind = _normalized_choice(
        receipt_kind, PROVENANCE_RECEIPT_KINDS
    )
    normalized_target = _normalize_target(target)
    payload = _digest_payload(
        project_id=normalized_project_id,
        task_id=normalized_task_id,
        review_receipt_id=normalized_receipt_id,
        receipt_kind=normalized_kind,
        target=normalized_target,
        provenance=provenance,
    )
    return "sha256:" + hashlib.sha256(
        DIGEST_DOMAIN + _canonical_json_bytes(payload)
    ).hexdigest()


def build_review_provenance_v1(
    *,
    project_id: Any,
    task_id: Any,
    review_receipt_id: Any,
    receipt_kind: Any,
    target: Any,
    normalized_input: Mapping[str, Any],
    review_provenance_id: Any = None,
) -> dict[str, Any]:
    """Build one exact public v1 object from normalized caller input."""

    if not isinstance(normalized_input, Mapping) or not _has_exact_keys(
        normalized_input, REVIEW_PROVENANCE_FIELDS[2:12]
    ):
        raise _invalid()
    normalized_kind = _normalized_choice(
        receipt_kind, PROVENANCE_RECEIPT_KINDS
    )
    verified_input = normalize_review_provenance_input(
        receipt_kind=normalized_kind,
        reviewer_class=normalized_input["reviewer_class"],
        model_state=normalized_input["model_state"],
        declared_model_id=normalized_input["declared_model_id"],
        skill_state=normalized_input["skill_state"],
        declared_skill_id=normalized_input["declared_skill_id"],
        declared_skill_version=normalized_input["declared_skill_version"],
        review_profiles=normalized_input["review_profiles"],
        review_lenses=normalized_input["review_lenses"],
        context_relation=normalized_input["context_relation"],
        method_codes=normalized_input["method_codes"],
    )
    if verified_input is None or any(
        normalized_input[field] != verified_input[field]
        for field in REVIEW_PROVENANCE_FIELDS[2:12]
    ):
        raise _invalid()
    provenance_id = (
        generate_review_provenance_id()
        if review_provenance_id is None
        else review_provenance_id
    )
    if (
        not isinstance(provenance_id, str)
        or REVIEW_PROVENANCE_ID_PATTERN.fullmatch(provenance_id) is None
    ):
        raise _invalid()
    provenance: dict[str, Any] = {
        "review_provenance_id": provenance_id,
        "provenance_version": 1,
        **{
            field: verified_input[field]
            for field in REVIEW_PROVENANCE_FIELDS[2:12]
        },
        "assurance_class": V1_ASSURANCE_CLASS,
        "producer_class": V1_PRODUCER_CLASS,
        "producer_version": PRODUCER_VERSION,
        "digest": "",
    }
    provenance["digest"] = review_provenance_digest(
        project_id=project_id,
        task_id=task_id,
        review_receipt_id=review_receipt_id,
        receipt_kind=normalized_kind,
        target=target,
        provenance=provenance,
    )
    return provenance


def legacy_review_provenance(receipt_kind: Any) -> dict[str, Any] | None:
    """Project exact version-zero absence or Tier-0 null provenance."""

    normalized_kind = _normalized_choice(receipt_kind, ALL_RECEIPT_KINDS)
    if normalized_kind == "not_required":
        return None
    return {
        "review_provenance_id": None,
        "provenance_version": 0,
        "reviewer_class": None,
        "model_state": None,
        "declared_model_id": None,
        "skill_state": None,
        "declared_skill_id": None,
        "declared_skill_version": None,
        "review_profiles": None,
        "review_lenses": None,
        "context_relation": None,
        "method_codes": None,
        "assurance_class": LEGACY_ASSURANCE_CLASS,
        "producer_class": LEGACY_PRODUCER_CLASS,
        "producer_version": PRODUCER_VERSION,
        "digest": None,
    }


def validate_stored_review_provenance_v1(
    provenance: Any,
    *,
    project_id: Any,
    task_id: Any,
    review_receipt_id: Any,
    receipt_kind: Any,
    target: Any,
) -> dict[str, Any]:
    """Validate and return one exact stored/public v1 provenance object."""

    from task_governance_tool.tasks import TaskValidationError

    try:
        if (
            not isinstance(provenance, Mapping)
            or not _has_exact_keys(provenance, REVIEW_PROVENANCE_FIELDS)
            or not isinstance(provenance["review_provenance_id"], str)
            or REVIEW_PROVENANCE_ID_PATTERN.fullmatch(
                provenance["review_provenance_id"]
            )
            is None
            or type(provenance["provenance_version"]) is not int
            or provenance["provenance_version"] != 1
            or provenance["assurance_class"] != V1_ASSURANCE_CLASS
            or provenance["producer_class"] != V1_PRODUCER_CLASS
            or type(provenance["producer_version"]) is not int
            or provenance["producer_version"] != PRODUCER_VERSION
            or not isinstance(provenance["digest"], str)
            or DIGEST_PATTERN.fullmatch(provenance["digest"]) is None
        ):
            raise _invalid()
        normalized = normalize_review_provenance_input(
            receipt_kind=receipt_kind,
            reviewer_class=provenance["reviewer_class"],
            model_state=provenance["model_state"],
            declared_model_id=provenance["declared_model_id"],
            skill_state=provenance["skill_state"],
            declared_skill_id=provenance["declared_skill_id"],
            declared_skill_version=provenance["declared_skill_version"],
            review_profiles=provenance["review_profiles"],
            review_lenses=provenance["review_lenses"],
            context_relation=provenance["context_relation"],
            method_codes=provenance["method_codes"],
        )
        if normalized is None or any(
            provenance[field] != normalized[field]
            for field in REVIEW_PROVENANCE_FIELDS[2:12]
        ):
            raise _invalid()
        expected_digest = review_provenance_digest(
            project_id=project_id,
            task_id=task_id,
            review_receipt_id=review_receipt_id,
            receipt_kind=receipt_kind,
            target=target,
            provenance=provenance,
        )
        if provenance["digest"] != expected_digest:
            raise _invalid()
        return {
            field: provenance[field]
            for field in REVIEW_PROVENANCE_FIELDS
        }
    except TaskValidationError as exc:
        raise _invalid() from exc


def project_review_provenance(
    *,
    receipt_kind: Any,
    basis_version: Any,
    provenance: Any,
    project_id: Any = None,
    task_id: Any = None,
    review_receipt_id: Any = None,
    target: Any = None,
) -> dict[str, Any] | None:
    """Project the exact v1/v0/null public union from a stored basis."""

    normalized_kind = _normalized_choice(receipt_kind, ALL_RECEIPT_KINDS)
    if type(basis_version) is not int or basis_version not in {0, 1}:
        raise _invalid()
    if normalized_kind == "not_required":
        if basis_version != 0 or provenance is not None:
            raise _invalid()
        return None
    if basis_version == 0:
        if provenance is not None:
            raise _invalid()
        return legacy_review_provenance(normalized_kind)
    if provenance is None:
        raise _invalid()
    return validate_stored_review_provenance_v1(
        provenance,
        project_id=project_id,
        task_id=task_id,
        review_receipt_id=review_receipt_id,
        receipt_kind=normalized_kind,
        target=target,
    )
