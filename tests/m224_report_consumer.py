"""Independent, read-only TG-M22.4 Evidence JSON report consumer.

This is test support, not a shipped taskgov feature.  It deliberately imports
neither SQLite nor the production Evidence projector/canonical encoder.  The
consumer validates the closed public JSON contract from bytes on disk and
reports only facts present in a referenced native Bundle.  A legacy index
entry remains index-only and never gains fabricated Receipt detail.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any


INDEX_MAX_BYTES = 67_108_864
BUNDLE_MAX_BYTES = 16_777_216
INDEX_DOMAIN = b"taskgov-evidence-index-v1\0"
BUNDLE_DOMAIN = b"taskgov-completion-evidence-bundle-v1\0"
INDEX_V2_DOMAIN = b"taskgov-evidence-index-v2\0"
BUNDLE_V2_DOMAIN = b"taskgov-completion-evidence-bundle-v2\0"
REVIEW_PROVENANCE_DOMAIN = b"taskgov-review-provenance-v1\0"
CONTRACT_CRITERION_DOMAIN = b"taskgov-contract-criterion-v1\0"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_TASK_ID = re.compile(r"^tg_task_[0-9a-f]{16}$")
_COMPLETION_CYCLE_ID = re.compile(r"^tg_completion_cycle_[0-9a-f]{16}$")
_BUNDLE_ID = re.compile(r"^tg_completion_evidence_bundle_[0-9a-f]{16}$")
_REVIEW_RECEIPT_ID = re.compile(r"^tg_review_receipt_[0-9a-f]{16}$")
_VERIFICATION_RECEIPT_ID = re.compile(
    r"^tg_verification_receipt_[0-9a-f]{16}$"
)
_AUTHORITY_ID = re.compile(r"^tg_authority_snapshot_[0-9a-f]{16}$")
_CRITERION_ID = re.compile(r"^tg_contract_criterion_[0-9a-f]{16}$")
_REFERENCE_ID = re.compile(r"^tg_evidence_reference_[0-9a-f]{16}$")
_LINK_ID = re.compile(r"^tg_criterion_evidence_link_[0-9a-f]{16}$")
_PROVENANCE_ID = re.compile(r"^tg_review_provenance_[0-9a-f]{16}$")
_DECLARED_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}$")
_DECLARED_SKILL_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")
_WINDOWS_REPARSE_ATTRIBUTE = 0x400

# These are test-owned authority literals, deliberately independent of the
# production Review-provenance module.
_REVIEWER_CLASSES = (
    "human",
    "llm",
    "deterministic_tool",
    "hybrid",
    "unknown",
)
_MODEL_STATES = ("declared", "not_applicable", "unknown")
_SKILL_STATES = ("declared", "not_applicable", "not_used", "unknown")
_CONTEXT_RELATIONS = (
    "same_context",
    "forked_context",
    "fresh_context",
    "external_context",
    "not_applicable",
    "unknown",
)
_REVIEW_PROFILES = (
    "general",
    "authority_contract",
    "implementation",
    "verification",
    "migration_compatibility",
    "privacy_safety",
    "release_acceptance",
)
_REVIEW_LENSES = (
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
_REVIEW_METHODS = (
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

_INDEX_ENVELOPE_KEYS = ("format_version", "index_digest", "payload")
_INDEX_PAYLOAD_KEYS = (
    "source_schema_version",
    "project_id",
    "projection_generation",
    "bundle_count",
    "legacy_count",
    "entries",
)
_INDEX_ENTRY_KEYS = (
    "task_id",
    "completion_cycle_id",
    "cycle_ordinal",
    "bundle_state",
    "bundle_id",
    "bundle_file",
    "bundle_digest",
    "file_digest",
    "sealed_at",
)
_INDEX_ENTRY_V2_KEYS = _INDEX_ENTRY_KEYS + ("bundle_format_version",)
_BUNDLE_ENVELOPE_KEYS = ("bundle_digest", "format_version", "payload")
_BUNDLE_PAYLOAD_KEYS = (
    "artifact_manifest",
    "authority_snapshot",
    "bundle_id",
    "bundle_version",
    "completion_cycle_id",
    "cycle_ordinal",
    "sealed_at",
    "completion_evidence",
    "contract",
    "criteria",
    "criterion_links",
    "evidence_references",
    "finding_snapshots",
    "omissions",
    "project_id",
    "review_receipts",
    "source_schema_version",
    "target",
    "task",
    "verification_receipt",
)
_BUNDLE_PAYLOAD_V2_KEYS = _BUNDLE_PAYLOAD_KEYS + (
    "verification_basis",
    "runner_observation",
)
_VERIFICATION_BASIS_KEYS = (
    "basis_version",
    "kind",
    "runner_observation_id",
    "verification_receipt_id",
)
_ARTIFACT_MANIFEST_KEYS = (
    "artifact_manifest_id",
    "state",
    "object_format",
    "comparison_base",
    "digest",
    "omission_code",
    "entries",
)
_ARTIFACT_ENTRY_KEYS = (
    "ordinal",
    "kind",
    "old_path",
    "new_path",
    "before_mode",
    "before_object_id",
    "after_mode",
    "after_object_id",
)
_AUTHORITY_SNAPSHOT_KEYS = (
    "authority_snapshot_id",
    "generation",
    "digest",
)
_COMPLETION_EVIDENCE_KEYS = (
    "kind",
    "revision",
    "reason",
    "external_revision_approved",
    "completion_commit_required",
    "completion_commit_hash",
)
_CONTRACT_KEYS = (
    "revision",
    "specified",
    "scope",
    "acceptance",
    "constraints",
    "authority_ref",
)
_CRITERION_KEYS = ("criterion_id", "kind", "text", "digest")
_CRITERION_LINK_KEYS = (
    "criterion_evidence_link_id",
    "criterion_id",
    "evidence_reference_id",
    "relation",
    "assurance_class",
    "producer_class",
    "producer_version",
)
_EVIDENCE_REFERENCE_KEYS = (
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
)
_FINDING_SNAPSHOT_KEYS = (
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
)
_REVIEW_RECEIPT_KEYS = (
    "review_receipt_id",
    "reviewer_key",
    "receipt_kind",
    "verdict",
    "summary",
    "user_approved",
    "created_at",
    "review_provenance",
)
_REVIEW_PROVENANCE_KEYS = (
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
_TARGET_KEYS = (
    "kind",
    "value",
    "base_revision",
    "generation",
    "capture_version",
)
_TASK_KEYS = ("task_id", "title", "description", "review_tier", "verification")
_VERIFICATION_RECEIPT_KEYS = (
    "verification_receipt_id",
    "verification_subject",
    "result",
    "duration_ms",
    "scope_coverage",
    "created_at",
)
_VERIFICATION_SUBJECT_KEYS = (
    "basis_version",
    "kind",
    "authority_snapshot_id",
    "verification_criterion_id",
)


class EvidenceReportError(ValueError):
    """Fixed, sanitized rejection for an invalid report input tree."""

    code = "evidence_report_invalid"

    def __init__(self) -> None:
        super().__init__("Evidence report input is invalid")


def _invalid() -> EvidenceReportError:
    return EvidenceReportError()


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_nonnegative_int(value: Any) -> bool:
    return _is_int(value) and value >= 0


def _is_positive_int(value: Any) -> bool:
    return _is_int(value) and value > 0


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _mapping(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise _invalid()
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise _invalid()
    return value


def _string(value: Any, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str):
        raise _invalid()
    return value


def _validate_unicode(value: Any) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _invalid() from exc
        return
    if isinstance(value, list):
        for item in value:
            _validate_unicode(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_unicode(key)
            _validate_unicode(item)
        return
    raise _invalid()


def _canonical_json_bytes(value: Any) -> bytes:
    """Independent reference encoding for already parsed JSON values."""

    _validate_unicode(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _invalid() from exc


def _criterion_digest(kind: str, text: str) -> str:
    if kind not in {"acceptance", "verification"} or not text.strip():
        raise _invalid()
    return "sha256:" + hashlib.sha256(
        CONTRACT_CRITERION_DOMAIN
        + kind.encode("utf-8")
        + b"\0"
        + text.encode("utf-8")
    ).hexdigest()


def _pairs_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _reject_float(_value: str) -> None:
    raise _invalid()


def _parse_document(document: bytes) -> Any:
    if len(document) < 2 or not document.endswith(b"\n"):
        raise _invalid()
    body = document[:-1]
    try:
        decoded = body.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_pairs_without_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _invalid() from exc
    _validate_unicode(value)
    if _canonical_json_bytes(value) != body:
        raise _invalid()
    return value


def _has_reparse_attribute(details: os.stat_result) -> bool:
    return bool(
        int(getattr(details, "st_file_attributes", 0))
        & _WINDOWS_REPARSE_ATTRIBUTE
    )


def _file_stamp(details: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(details.st_size),
        int(details.st_mtime_ns),
    )


def _is_plain_bounded_file(details: os.stat_result, *, maximum: int) -> bool:
    return (
        stat.S_ISREG(details.st_mode)
        and not stat.S_ISLNK(details.st_mode)
        and not _has_reparse_attribute(details)
        and details.st_size <= maximum
    )


def _require_plain_directory(path: Path) -> Path:
    try:
        details = path.lstat()
    except OSError as exc:
        raise _invalid() from exc
    if (
        not stat.S_ISDIR(details.st_mode)
        or stat.S_ISLNK(details.st_mode)
        or _has_reparse_attribute(details)
    ):
        raise _invalid()
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise _invalid() from exc


def _read_plain_file(path: Path, *, maximum: int) -> bytes:
    try:
        path_details = path.lstat()
    except OSError as exc:
        raise _invalid() from exc
    if not _is_plain_bounded_file(path_details, maximum=maximum):
        raise _invalid()
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not _is_plain_bounded_file(before, maximum=maximum):
                raise _invalid()
            if _file_stamp(path_details) != _file_stamp(before):
                raise _invalid()
            # Read the observed size plus one byte to detect growth without
            # allocating the full 64 MiB ceiling for a tiny index.
            document = stream.read(min(maximum + 1, before.st_size + 1))
            after = os.fstat(stream.fileno())
    except EvidenceReportError:
        raise
    except OSError as exc:
        raise _invalid() from exc
    if (
        not _is_plain_bounded_file(after, maximum=maximum)
        or _file_stamp(before) != _file_stamp(after)
        or len(document) != before.st_size
    ):
        raise _invalid()
    try:
        final_path_details = path.lstat()
    except OSError as exc:
        raise _invalid() from exc
    if (
        not _is_plain_bounded_file(final_path_details, maximum=maximum)
        or _file_stamp(after) != _file_stamp(final_path_details)
    ):
        raise _invalid()
    return document


def _validate_simple_object_list(
    value: Any,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _list(value):
        result.append(_mapping(item, keys))
    return result


def _identifier(value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _invalid()
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _invalid() from exc
    return value


def _optional_identifier(value: Any, pattern: re.Pattern[str]) -> str | None:
    if value is None:
        return None
    return _identifier(value, pattern)


def _ordered_codes(
    value: Any,
    *,
    allowed: tuple[str, ...],
    maximum: int,
) -> list[str]:
    items = _list(value)
    if (
        len(items) > maximum
        or any(not isinstance(item, str) or item not in allowed for item in items)
        or len(items) != len(set(items))
    ):
        raise _invalid()
    selected = set(items)
    if items != [code for code in allowed if code in selected]:
        raise _invalid()
    return items


def _validate_provenance_semantics(projected: Mapping[str, Any]) -> None:
    reviewer_class = projected["reviewer_class"]
    model_state = projected["model_state"]
    skill_state = projected["skill_state"]
    context_relation = projected["context_relation"]
    if (
        reviewer_class not in _REVIEWER_CLASSES
        or model_state not in _MODEL_STATES
        or skill_state not in _SKILL_STATES
        or context_relation not in _CONTEXT_RELATIONS
    ):
        raise _invalid()

    model_id = _optional_identifier(
        projected["declared_model_id"],
        _DECLARED_IDENTIFIER,
    )
    skill_id = _optional_identifier(
        projected["declared_skill_id"],
        _DECLARED_IDENTIFIER,
    )
    skill_version = _optional_identifier(
        projected["declared_skill_version"],
        _DECLARED_SKILL_VERSION,
    )
    _ordered_codes(
        projected["review_profiles"],
        allowed=_REVIEW_PROFILES,
        maximum=4,
    )
    _ordered_codes(
        projected["review_lenses"],
        allowed=_REVIEW_LENSES,
        maximum=8,
    )
    _ordered_codes(
        projected["method_codes"],
        allowed=_REVIEW_METHODS,
        maximum=8,
    )

    model_declared = model_state == "declared" and model_id is not None
    model_unknown = model_state == "unknown" and model_id is None
    skill_declared = (
        skill_state == "declared"
        and skill_id is not None
        and skill_version is not None
    )
    skill_without_declaration = (
        skill_state in {"not_used", "unknown"}
        and skill_id is None
        and skill_version is None
    )
    if reviewer_class in {"human", "deterministic_tool"}:
        valid = (
            model_state == "not_applicable"
            and model_id is None
            and skill_state == "not_applicable"
            and skill_id is None
            and skill_version is None
        )
    elif reviewer_class in {"llm", "hybrid"}:
        valid = (model_declared or model_unknown) and (
            skill_declared or skill_without_declaration
        )
    else:
        valid = (
            model_state == "unknown"
            and model_id is None
            and skill_state == "unknown"
            and skill_id is None
            and skill_version is None
        )
    if not valid:
        raise _invalid()


def _validate_review_provenance(
    provenance: Any,
    *,
    project_id: str,
    task_id: str,
    target: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any] | None:
    kind = receipt["receipt_kind"]
    if provenance is None:
        if kind != "not_required":
            raise _invalid()
        return None
    if kind not in {"independent", "self_review_fallback"}:
        raise _invalid()
    projected = _mapping(provenance, _REVIEW_PROVENANCE_KEYS)
    if (
        not _is_int(projected["provenance_version"])
        or projected["provenance_version"] != 1
        or projected["assurance_class"] != "bound_attestation"
        or projected["producer_class"] != "trusted_caller"
        or not _is_int(projected["producer_version"])
        or projected["producer_version"] != 1
        or not _is_digest(projected["digest"])
    ):
        raise _invalid()
    _identifier(projected["review_provenance_id"], _PROVENANCE_ID)
    _validate_provenance_semantics(projected)
    digest_target = dict(target)
    if digest_target["base_revision"] is None:
        # SQLite seals its empty-string sentinel.  Evidence JSON deliberately
        # projects that sentinel as null, so the independent consumer must
        # reconstruct the sealed target without importing production code.
        digest_target["base_revision"] = ""
    digest_payload = {
        "project_id": project_id,
        "task_id": task_id,
        "review_receipt_id": receipt["review_receipt_id"],
        "receipt_kind": kind,
        "target": digest_target,
        **{
            field: projected[field]
            for field in _REVIEW_PROVENANCE_KEYS[1:-1]
        },
    }
    expected_digest = "sha256:" + hashlib.sha256(
        REVIEW_PROVENANCE_DOMAIN + _canonical_json_bytes(digest_payload)
    ).hexdigest()
    if projected["digest"] != expected_digest:
        raise _invalid()
    return projected


def _validate_review_receipts(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    task = _mapping(payload["task"], _TASK_KEYS)
    target = _mapping(payload["target"], _TARGET_KEYS)
    review_tier = task["review_tier"]
    seen_receipt_ids: set[str] = set()
    gate_basis: list[tuple[str, str, str]] = []
    for raw_receipt in _list(payload["review_receipts"]):
        receipt = _mapping(raw_receipt, _REVIEW_RECEIPT_KEYS)
        receipt_id = _identifier(receipt["review_receipt_id"], _REVIEW_RECEIPT_ID)
        if receipt_id in seen_receipt_ids:
            raise _invalid()
        seen_receipt_ids.add(receipt_id)
        reviewer_key = _string(receipt["reviewer_key"])
        summary = _string(receipt["summary"])
        _string(receipt["created_at"])
        kind = _string(receipt["receipt_kind"])
        verdict = _string(receipt["verdict"])
        if (
            not reviewer_key.strip()
            or reviewer_key != reviewer_key.strip()
            or len(reviewer_key) > 500
            or len(summary) > 1_000
            or kind not in {"independent", "self_review_fallback", "not_required"}
            or verdict not in {"pass", "changes_requested", "not_required"}
        ):
            raise _invalid()
        if not _is_int(receipt["user_approved"]) or receipt["user_approved"] not in {
            0,
            1,
        }:
            raise _invalid()
        approval = receipt["user_approved"]
        if kind == "independent":
            valid_relation = verdict == "pass" and approval == 0
        elif kind == "self_review_fallback":
            valid_relation = (
                review_tier in {1, 2}
                and verdict == "pass"
                and bool(summary.strip())
                and approval == int(review_tier == 2)
            )
        else:
            valid_relation = (
                review_tier == 0
                and verdict == "not_required"
                and approval == 0
                and bool(summary.strip())
            )
        if not valid_relation:
            raise _invalid()
        provenance = _validate_review_provenance(
            receipt["review_provenance"],
            project_id=str(payload["project_id"]),
            task_id=str(task["task_id"]),
            target=target,
            receipt=receipt,
        )
        report = {
            "review_receipt_id": receipt["review_receipt_id"],
            "receipt_kind": receipt["receipt_kind"],
            "verdict": receipt["verdict"],
            "provenance_state": "not_required" if provenance is None else "v1",
        }
        if provenance is not None:
            report.update(
                {
                    "reviewer_class": provenance["reviewer_class"],
                    "model_state": provenance["model_state"],
                    "declared_model_id": provenance["declared_model_id"],
                    "skill_state": provenance["skill_state"],
                    "declared_skill_id": provenance["declared_skill_id"],
                    "declared_skill_version": provenance[
                        "declared_skill_version"
                    ],
                    "review_profiles": list(provenance["review_profiles"]),
                    "review_lenses": list(provenance["review_lenses"]),
                    "context_relation": provenance["context_relation"],
                    "method_codes": list(provenance["method_codes"]),
                }
            )
        reports.append(report)
        gate_basis.append((kind, reviewer_key, receipt_id))

    if review_tier == 0:
        valid_basis = (
            len(gate_basis) == 1
            and gate_basis[0][0] == "not_required"
        )
    elif review_tier == 1:
        valid_basis = (
            len(gate_basis) == 1
            and gate_basis[0][0] in {"independent", "self_review_fallback"}
        )
    else:
        fallback_basis = (
            len(gate_basis) == 1
            and gate_basis[0][0] == "self_review_fallback"
        )
        independent_basis = (
            len(gate_basis) == 2
            and all(item[0] == "independent" for item in gate_basis)
            and len({item[1] for item in gate_basis}) == 2
            and gate_basis
            == sorted(
                gate_basis,
                key=lambda item: (
                    item[1].encode("utf-8"),
                    item[2].encode("utf-8"),
                ),
            )
        )
        valid_basis = fallback_basis or independent_basis
    if not valid_basis:
        raise _invalid()
    return reports


def _validate_bundle_payload(
    value: Any,
    *,
    entry: Mapping[str, Any],
    project_id: str,
    bundle_format_version: int,
    maximum_source_schema_version: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if bundle_format_version not in {1, 2}:
        raise _invalid()
    payload = _mapping(
        value,
        (
            _BUNDLE_PAYLOAD_KEYS
            if bundle_format_version == 1
            else _BUNDLE_PAYLOAD_V2_KEYS
        ),
    )
    source_schema_version = payload["source_schema_version"]
    if (
        not _is_int(source_schema_version)
        or (source_schema_version, bundle_format_version)
        not in {(19, 1), (20, 2), (21, 2)}
        or source_schema_version > maximum_source_schema_version
        or not _is_int(payload["bundle_version"])
        or payload["bundle_version"] != bundle_format_version
        or payload["project_id"] != project_id
        or payload["bundle_id"] != entry["bundle_id"]
        or payload["completion_cycle_id"] != entry["completion_cycle_id"]
        or payload["cycle_ordinal"] != entry["cycle_ordinal"]
        or payload["sealed_at"] != entry["sealed_at"]
    ):
        raise _invalid()
    task = _mapping(payload["task"], _TASK_KEYS)
    if task["task_id"] != entry["task_id"]:
        raise _invalid()
    _identifier(task["task_id"], _TASK_ID)
    for field in ("task_id", "title", "description", "verification"):
        _string(task[field])
    if not _is_int(task["review_tier"]) or task["review_tier"] not in {0, 1, 2}:
        raise _invalid()

    target = _mapping(payload["target"], _TARGET_KEYS)
    for field in ("kind", "value"):
        _string(target[field])
    _string(target["base_revision"], nullable=True)
    if (
        not _is_positive_int(target["generation"])
        or not _is_int(target["capture_version"])
        or target["capture_version"] != 1
    ):
        raise _invalid()

    manifest = _mapping(payload["artifact_manifest"], _ARTIFACT_MANIFEST_KEYS)
    _validate_simple_object_list(manifest["entries"], _ARTIFACT_ENTRY_KEYS)
    authority = _mapping(payload["authority_snapshot"], _AUTHORITY_SNAPSHOT_KEYS)
    if not _is_positive_int(authority["generation"]) or not _is_digest(
        authority["digest"]
    ):
        raise _invalid()
    completion = _mapping(payload["completion_evidence"], _COMPLETION_EVIDENCE_KEYS)
    for field in ("external_revision_approved", "completion_commit_required"):
        if not _is_int(completion[field]) or completion[field] not in {0, 1}:
            raise _invalid()
    contract = _mapping(payload["contract"], _CONTRACT_KEYS)
    if (
        not _is_nonnegative_int(contract["revision"])
        or type(contract["specified"]) is not bool
    ):
        raise _invalid()
    criteria = _validate_simple_object_list(payload["criteria"], _CRITERION_KEYS)
    criterion_links = _validate_simple_object_list(
        payload["criterion_links"],
        _CRITERION_LINK_KEYS,
    )
    evidence_references = _validate_simple_object_list(
        payload["evidence_references"],
        _EVIDENCE_REFERENCE_KEYS,
    )
    _validate_simple_object_list(payload["finding_snapshots"], _FINDING_SNAPSHOT_KEYS)
    omissions = _list(payload["omissions"])
    if any(not isinstance(item, str) for item in omissions) or len(omissions) != len(
        set(omissions)
    ):
        raise _invalid()

    verification = payload["verification_receipt"]
    verification_subject: dict[str, Any] | None = None
    if verification is not None:
        verification = _mapping(verification, _VERIFICATION_RECEIPT_KEYS)
        verification_subject = _mapping(
            verification["verification_subject"],
            _VERIFICATION_SUBJECT_KEYS,
        )
        if (
            not _is_int(verification_subject["basis_version"])
            or verification_subject["basis_version"] != 1
            or verification_subject["kind"] != "task_verification_criterion"
            or not _is_nonnegative_int(verification["duration_ms"])
        ):
            raise _invalid()

    if bundle_format_version == 2:
        authority_snapshot_id = _identifier(
            authority["authority_snapshot_id"],
            _AUTHORITY_ID,
        )
        criterion_ids: list[str] = []
        for criterion in criteria:
            criterion_id = _identifier(criterion["criterion_id"], _CRITERION_ID)
            criterion_ids.append(criterion_id)
            kind = _string(criterion["kind"])
            text = _string(criterion["text"])
            if (
                kind not in {"acceptance", "verification"}
                or criterion["digest"] != _criterion_digest(kind, text)
            ):
                raise _invalid()
        if len(criterion_ids) != len(set(criterion_ids)):
            raise _invalid()
        acceptance_criteria = [
            criterion for criterion in criteria if criterion["kind"] == "acceptance"
        ]
        verification_criteria = [
            criterion
            for criterion in criteria
            if criterion["kind"] == "verification"
        ]
        if contract["revision"] == 0:
            if acceptance_criteria:
                raise _invalid()
        else:
            if (
                len(acceptance_criteria) != 1
                or acceptance_criteria[0]["text"] != contract["acceptance"]
            ):
                raise _invalid()
        verification_specified = bool(task["verification"].strip())
        if verification_specified:
            if (
                len(verification_criteria) != 1
                or verification_criteria[0]["text"] != task["verification"]
            ):
                raise _invalid()
            verification_criterion_id = verification_criteria[0]["criterion_id"]
        else:
            if verification_criteria:
                raise _invalid()
            verification_criterion_id = None

        basis = _mapping(payload["verification_basis"], _VERIFICATION_BASIS_KEYS)
        expected_basis_kind = (
            "caller_attestation" if verification_specified else "not_required"
        )
        if (
            not _is_int(basis["basis_version"])
            or basis["basis_version"] != 1
            or basis["kind"] != expected_basis_kind
            or basis["runner_observation_id"] is not None
        ):
            raise _invalid()
        receipt_id = (
            verification["verification_receipt_id"]
            if verification is not None
            else None
        )
        if basis["kind"] == "caller_attestation":
            if (
                verification is None
                or verification_subject is None
                or basis["verification_receipt_id"] != receipt_id
                or verification_subject["authority_snapshot_id"]
                != authority_snapshot_id
                or verification_subject["verification_criterion_id"]
                != verification_criterion_id
            ):
                raise _invalid()
            _identifier(
                basis["verification_receipt_id"],
                _VERIFICATION_RECEIPT_ID,
            )
        elif verification is not None or basis["verification_receipt_id"] is not None:
            raise _invalid()

        reference_ids = [
            _identifier(reference["evidence_reference_id"], _REFERENCE_ID)
            for reference in evidence_references
        ]
        link_ids = [
            _identifier(link["criterion_evidence_link_id"], _LINK_ID)
            for link in criterion_links
        ]
        if (
            len(reference_ids) != len(set(reference_ids))
            or len(link_ids) != len(set(link_ids))
            or payload["runner_observation"] is not None
            or any(
                reference["source_kind"] == "runner_observation"
                for reference in evidence_references
            )
            or any(
                link["relation"] == "runner_observation"
                for link in criterion_links
            )
        ):
            raise _invalid()

    return payload, _validate_review_receipts(payload)


def _read_native_bundle(
    evidence_root: Path,
    bundles_root: Path,
    *,
    entry: Mapping[str, Any],
    project_id: str,
    maximum_source_schema_version: int,
) -> list[dict[str, Any]]:
    bundle_id = entry["bundle_id"]
    bundle_file = entry["bundle_file"]
    if (
        not isinstance(bundle_id, str)
        or _BUNDLE_ID.fullmatch(bundle_id) is None
        or not isinstance(bundle_file, str)
    ):
        raise _invalid()
    relative = PurePosixPath(bundle_file)
    expected_name = f"{bundle_id}.json"
    if relative.parts != ("bundles", expected_name):
        raise _invalid()
    bundle_path = evidence_root / "bundles" / expected_name
    try:
        resolved = bundle_path.resolve(strict=True)
    except OSError as exc:
        raise _invalid() from exc
    if resolved.parent != bundles_root or resolved.name != expected_name:
        raise _invalid()
    document = _read_plain_file(bundle_path, maximum=BUNDLE_MAX_BYTES)
    if entry["file_digest"] != "sha256:" + hashlib.sha256(document).hexdigest():
        raise _invalid()
    expected_format = entry.get("bundle_format_version", 1)
    if type(expected_format) is not int or expected_format not in {1, 2}:
        raise _invalid()
    envelope = _mapping(_parse_document(document), _BUNDLE_ENVELOPE_KEYS)
    if (
        not _is_int(envelope["format_version"])
        or envelope["format_version"] != expected_format
        or envelope["bundle_digest"] != entry["bundle_digest"]
    ):
        raise _invalid()
    payload, receipts = _validate_bundle_payload(
        envelope["payload"],
        entry=entry,
        project_id=project_id,
        bundle_format_version=expected_format,
        maximum_source_schema_version=maximum_source_schema_version,
    )
    expected_digest = "sha256:" + hashlib.sha256(
        (BUNDLE_DOMAIN if expected_format == 1 else BUNDLE_V2_DOMAIN)
        + _canonical_json_bytes(payload)
    ).hexdigest()
    if envelope["bundle_digest"] != expected_digest:
        raise _invalid()
    return receipts


def _validate_index_entry(entry: Mapping[str, Any], *, format_version: int) -> None:
    _identifier(entry["task_id"], _TASK_ID)
    _identifier(entry["completion_cycle_id"], _COMPLETION_CYCLE_ID)
    _string(entry["bundle_state"])
    if not _is_positive_int(entry["cycle_ordinal"]):
        raise _invalid()
    state = entry["bundle_state"]
    bundle_fields = (
        "bundle_id",
        "bundle_file",
        "bundle_digest",
        "file_digest",
        "sealed_at",
    )
    if state == "legacy_unknown":
        if any(entry[field] is not None for field in bundle_fields) or (
            format_version == 2 and entry["bundle_format_version"] is not None
        ):
            raise _invalid()
        return
    if state != "native" or any(
        not isinstance(entry[field], str) for field in bundle_fields
    ):
        raise _invalid()
    _identifier(entry["bundle_id"], _BUNDLE_ID)
    if (
        not entry["sealed_at"]
        or not _is_digest(entry["bundle_digest"])
        or not _is_digest(entry["file_digest"])
        or (
            format_version == 2
            and (
                not _is_int(entry["bundle_format_version"])
                or entry["bundle_format_version"] not in {1, 2}
            )
        )
    ):
        raise _invalid()


def read_evidence_report(
    evidence_root: str | os.PathLike[str],
    *,
    expected_project_id: str | None = None,
) -> dict[str, Any]:
    """Validate one fixed Evidence tree and return a bounded factual report.

    The declared projection generation is not described as current because a
    standalone consumer cannot compare it with the inaccessible canonical DB.
    No filesystem write or enumeration of unreferenced Bundle files occurs.
    """

    root = Path(evidence_root)
    root_resolved = _require_plain_directory(root)
    index_path = root / "index.json"
    try:
        if index_path.resolve(strict=True).parent != root_resolved:
            raise _invalid()
    except OSError as exc:
        raise _invalid() from exc
    index_document = _read_plain_file(index_path, maximum=INDEX_MAX_BYTES)
    envelope = _mapping(_parse_document(index_document), _INDEX_ENVELOPE_KEYS)
    payload = _mapping(envelope["payload"], _INDEX_PAYLOAD_KEYS)
    format_version = envelope["format_version"]
    if (
        not _is_int(format_version)
        or (format_version, payload["source_schema_version"])
        not in {(1, 19), (2, 20), (2, 21)}
    ):
        raise _invalid()
    project_id = _string(payload["project_id"])
    if not project_id or (
        expected_project_id is not None and project_id != expected_project_id
    ):
        raise _invalid()
    if not _is_nonnegative_int(payload["projection_generation"]):
        raise _invalid()
    if not _is_nonnegative_int(payload["bundle_count"]) or not _is_nonnegative_int(
        payload["legacy_count"]
    ):
        raise _invalid()
    if not _is_digest(envelope["index_digest"]):
        raise _invalid()
    expected_index_digest = "sha256:" + hashlib.sha256(
        (INDEX_DOMAIN if format_version == 1 else INDEX_V2_DOMAIN)
        + _canonical_json_bytes(payload)
    ).hexdigest()
    if envelope["index_digest"] != expected_index_digest:
        raise _invalid()

    entries = [
        _mapping(
            item,
            _INDEX_ENTRY_KEYS if format_version == 1 else _INDEX_ENTRY_V2_KEYS,
        )
        for item in _list(payload["entries"])
    ]
    if len(entries) > 100_000:
        raise _invalid()
    for entry in entries:
        _validate_index_entry(entry, format_version=format_version)
    expected_order = sorted(
        entries,
        key=lambda item: (
            str(item["task_id"]).encode("utf-8"),
            int(item["cycle_ordinal"]),
            str(item["completion_cycle_id"]).encode("utf-8"),
        ),
    )
    if entries != expected_order:
        raise _invalid()
    native_count = sum(item["bundle_state"] == "native" for item in entries)
    legacy_count = sum(
        item["bundle_state"] == "legacy_unknown" for item in entries
    )
    if (
        native_count != payload["bundle_count"]
        or legacy_count != payload["legacy_count"]
        or native_count + legacy_count != len(entries)
    ):
        raise _invalid()

    bundles_root: Path | None = None
    if native_count:
        bundles_root = _require_plain_directory(root / "bundles")
        if bundles_root.parent != root_resolved:
            raise _invalid()

    report_entries: list[dict[str, Any]] = []
    profile_counts: Counter[str] = Counter()
    lens_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    seen_cycle_ids: set[str] = set()
    seen_task_ordinals: set[tuple[str, int]] = set()
    seen_bundles: set[str] = set()
    seen_receipts: set[str] = set()
    for entry in entries:
        cycle_id = str(entry["completion_cycle_id"])
        task_ordinal = (str(entry["task_id"]), int(entry["cycle_ordinal"]))
        if cycle_id in seen_cycle_ids or task_ordinal in seen_task_ordinals:
            raise _invalid()
        seen_cycle_ids.add(cycle_id)
        seen_task_ordinals.add(task_ordinal)
        report_entry = {
            "task_id": entry["task_id"],
            "completion_cycle_id": entry["completion_cycle_id"],
            "cycle_ordinal": entry["cycle_ordinal"],
            "bundle_state": entry["bundle_state"],
        }
        if entry["bundle_state"] == "native":
            bundle_id = str(entry["bundle_id"])
            if bundle_id in seen_bundles or bundles_root is None:
                raise _invalid()
            seen_bundles.add(bundle_id)
            receipts = _read_native_bundle(
                root,
                bundles_root,
                entry=entry,
                project_id=project_id,
                maximum_source_schema_version=payload["source_schema_version"],
            )
            report_entry["bundle_id"] = bundle_id
            report_entry["review_receipts"] = receipts
            for receipt in receipts:
                receipt_id = str(receipt["review_receipt_id"])
                if receipt_id in seen_receipts:
                    raise _invalid()
                seen_receipts.add(receipt_id)
                if receipt["provenance_state"] != "v1":
                    continue
                profile_counts.update(receipt["review_profiles"])
                lens_counts.update(receipt["review_lenses"])
                method_counts.update(receipt["method_codes"])
        # Deliberately do not add any Receipt-shaped key for legacy_unknown.
        report_entries.append(report_entry)

    def ordered_counts(
        counter: Counter[str],
        allowed: tuple[str, ...],
    ) -> dict[str, int]:
        return {key: counter[key] for key in allowed if counter[key]}

    return {
        "format_version": format_version,
        "project_id": project_id,
        "declared_projection_generation": payload["projection_generation"],
        "bundle_count": native_count,
        "legacy_count": legacy_count,
        "entries": report_entries,
        "code_occurrences": {
            "review_profiles": ordered_counts(
                profile_counts,
                _REVIEW_PROFILES,
            ),
            "review_lenses": ordered_counts(lens_counts, _REVIEW_LENSES),
            "method_codes": ordered_counts(method_counts, _REVIEW_METHODS),
        },
    }


__all__ = (
    "BUNDLE_MAX_BYTES",
    "EvidenceReportError",
    "INDEX_MAX_BYTES",
    "read_evidence_report",
)
