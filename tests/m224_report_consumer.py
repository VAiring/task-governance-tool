"""Read-only TG-M22.4 report adapter over the independent Evidence oracle.

The oracle owns canonical bytes, version/domain selection and closed semantic
validation. This test-only adapter projects already validated Receipt facts;
it imports neither storage nor the producing Evidence semantic validator.
Legacy entries stay index-only. Collected provenance remains v2 and never
supplies the v1 caller-declaration fields or code counts.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterator, Mapping
from typing import Any

from tests.evidence_reader_oracle import (
    EvidenceConsumerError,
    ValidatedEvidenceIndex,
    ValidatedEvidenceSource,
    iter_evidence_sources,
    read_evidence_index,
)


INDEX_MAX_BYTES = 67_108_864
BUNDLE_MAX_BYTES = 16_777_216

# Retain the report's public code order, independently of producer internals.
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
_V1_REPORT_FIELDS = (
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
)


class EvidenceReportError(ValueError):
    """Fixed, sanitized rejection for an invalid report input tree."""

    code = "evidence_report_invalid"

    def __init__(self) -> None:
        super().__init__("Evidence report input is invalid")


def _report_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    provenance = receipt["review_provenance"]
    report = {
        "review_receipt_id": receipt["review_receipt_id"],
        "receipt_kind": receipt["receipt_kind"],
        "verdict": receipt["verdict"],
        "provenance_state": (
            "not_required" if provenance is None
            else f"v{provenance['provenance_version']}"
        ),
    }
    if provenance is not None and provenance["provenance_version"] == 1:
        report.update({field: provenance[field] for field in _V1_REPORT_FIELDS})
    return report


def _report_sources(index: ValidatedEvidenceIndex) -> Iterator[ValidatedEvidenceSource]:
    try:
        yield from iter_evidence_sources(index)
    except (TypeError, KeyError) as exc:
        # Normalize malformed graph lookup/regex failures only while obtaining
        # sources, never around the caller's report projection/aggregation.
        raise EvidenceReportError() from exc


def read_evidence_report(
    evidence_root: str | os.PathLike[str],
    *,
    expected_project_id: str | None = None,
) -> dict[str, Any]:
    """Validate a fixed Evidence tree and project its bounded factual report.

    Declared generation is not described as current: a standalone reader has
    no canonical database. Unreferenced Bundle files are never enumerated.
    """

    try:
        index = read_evidence_index(
            evidence_root, expected_project_id=expected_project_id,
        )
        report_entries: list[dict[str, Any]] = []
        profile_counts: Counter[str] = Counter()
        lens_counts: Counter[str] = Counter()
        method_counts: Counter[str] = Counter()
        seen_bundles: set[str] = set()
        seen_receipts: set[str] = set()
        for validated_source in _report_sources(index):
            entry = validated_source.source_basis["entry"]
            source = validated_source.source
            report_entry = {
                field: entry[field]
                for field in (
                    "task_id", "completion_cycle_id", "cycle_ordinal", "bundle_state",
                )
            }
            if source is not None:
                bundle_id = entry["bundle_id"]
                if bundle_id in seen_bundles:
                    raise EvidenceReportError()
                seen_bundles.add(bundle_id)
                receipts = [
                    _report_receipt(receipt)
                    for receipt in source["payload"]["review_receipts"]
                ]
                report_entry["bundle_id"] = bundle_id
                report_entry["review_receipts"] = receipts
                for receipt in receipts:
                    receipt_id = receipt["review_receipt_id"]
                    if receipt_id in seen_receipts:
                        raise EvidenceReportError()
                    seen_receipts.add(receipt_id)
                    if receipt["provenance_state"] == "v1":
                        profile_counts.update(receipt["review_profiles"])
                        lens_counts.update(receipt["review_lenses"])
                        method_counts.update(receipt["method_codes"])
            # A legacy entry deliberately has no Receipt-shaped key.
            report_entries.append(report_entry)
    except EvidenceConsumerError as exc:
        raise EvidenceReportError() from exc

    def ordered_counts(counter: Counter[str], allowed: tuple[str, ...]) -> dict[str, int]:
        return {key: counter[key] for key in allowed if counter[key]}

    native_count = len(seen_bundles)
    return {
        "format_version": index.format_version,
        "project_id": index.project_id,
        "declared_projection_generation": index.projection_generation,
        "bundle_count": native_count,
        "legacy_count": len(report_entries) - native_count,
        "entries": report_entries,
        "code_occurrences": {
            "review_profiles": ordered_counts(profile_counts, _REVIEW_PROFILES),
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
