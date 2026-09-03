"""Reusable Evidence fixtures with a test-owned reference JSON encoder."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from tests.test_m223_bundle_assembly_pure import sample_payload


BUNDLE_DOMAIN = b"taskgov-completion-evidence-bundle-v1\0"
INDEX_DOMAIN = b"taskgov-evidence-index-v1\0"
CRITERION_DOMAIN = b"taskgov-contract-criterion-v1\0"
ARTIFACT_MANIFEST_DOMAIN = b"taskgov-artifact-manifest-v1\0"
EVIDENCE_REFERENCE_DOMAIN = b"taskgov-evidence-reference-v1\0"
REVIEW_PROVENANCE_DOMAIN = b"taskgov-review-provenance-v1\0"


def reference_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def domain_digest(domain: bytes, value: object) -> str:
    return "sha256:" + hashlib.sha256(domain + reference_json_bytes(value)).hexdigest()


def _reference_source_projection(
    payload: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    source_kind = reference["source_kind"]
    if source_kind == "artifact_manifest":
        artifact = payload["artifact_manifest"]
        if artifact["state"] == "complete_git":
            return {
                "artifact_manifest_id": artifact["artifact_manifest_id"],
                "state": artifact["state"],
                "object_format": artifact["object_format"],
                "comparison_base": artifact["comparison_base"],
                "entry_count": len(artifact["entries"]),
                "digest": artifact["digest"],
                "omission_code": artifact["omission_code"],
            }
        return {
            "artifact_manifest_id": artifact["artifact_manifest_id"],
            "state": artifact["state"],
            "target_kind": payload["target"]["kind"],
            "digest": artifact["digest"],
            "omission_code": artifact["omission_code"],
        }
    if source_kind == "verification_receipt":
        receipt = payload["verification_receipt"]
        subject = receipt["verification_subject"]
        return {
            "verification_receipt_id": receipt["verification_receipt_id"],
            "subject_basis_version": subject["basis_version"],
            "authority_snapshot_id": subject["authority_snapshot_id"],
            "verification_criterion_id": subject["verification_criterion_id"],
            "result": receipt["result"],
            "duration_ms": receipt["duration_ms"],
            "scope_coverage": receipt["scope_coverage"],
            "created_at": receipt["created_at"],
        }
    if source_kind == "review_receipt":
        return dict(
            next(
                receipt
                for receipt in payload["review_receipts"]
                if receipt["review_receipt_id"] == reference["source_id"]
            )
        )
    if source_kind == "review_finding":
        finding = next(
            item
            for item in payload["finding_snapshots"]
            if item["review_finding_id"] == reference["source_id"]
        )
        return {
            key: finding[key]
            for key in (
                "review_finding_id",
                "review_receipt_id",
                "severity",
                "summary",
                "created_at",
            )
        }
    completion = payload["completion_evidence"]
    return {
        "completion_cycle_id": payload["completion_cycle_id"],
        "completed_at": payload["sealed_at"],
        "completion_evidence_kind": completion["kind"],
        "completion_evidence_revision": completion["revision"],
        "completion_evidence_reason": completion["reason"],
        "external_revision_approved": completion["external_revision_approved"],
        "completion_commit_required": completion["completion_commit_required"],
        "completion_commit_hash": completion["completion_commit_hash"],
    }


def refresh_inner_digests(payload: dict[str, Any]) -> None:
    for criterion in payload["criteria"]:
        body = (
            CRITERION_DOMAIN
            + criterion["kind"].encode("utf-8")
            + b"\0"
            + criterion["text"].encode("utf-8")
        )
        criterion["digest"] = "sha256:" + hashlib.sha256(body).hexdigest()

    criteria_by_kind = {item["kind"]: item for item in payload["criteria"]}
    target = payload["target"]
    artifact = payload["artifact_manifest"]
    artifact_value = {
        "acceptance_criterion_id": criteria_by_kind.get("acceptance", {}).get(
            "criterion_id"
        ),
        "authority_snapshot_id": payload["authority_snapshot"][
            "authority_snapshot_id"
        ],
        "comparison_base": artifact["comparison_base"],
        "entries": artifact["entries"],
        "object_format": artifact["object_format"],
        "omission_code": artifact["omission_code"],
        "state": artifact["state"],
        "target_base_revision": target["base_revision"] or "",
        "target_generation": target["generation"],
        "target_kind": target["kind"],
        "target_value": target["value"],
        "verification_criterion_id": criteria_by_kind.get("verification", {}).get(
            "criterion_id"
        ),
    }
    artifact["digest"] = domain_digest(
        ARTIFACT_MANIFEST_DOMAIN,
        artifact_value,
    )

    for reference in payload["evidence_references"]:
        value = {
            "acceptance_criterion_id": reference["acceptance_criterion_id"],
            "assurance_class": reference["assurance_class"],
            "authority_snapshot_id": reference["authority_snapshot_id"],
            "completion_cycle_id": reference["completion_cycle_id"],
            "contract_revision": reference["contract_revision"],
            "producer_class": reference["producer_class"],
            "producer_version": reference["producer_version"],
            "project_id": payload["project_id"],
            "source_id": reference["source_id"],
            "source_kind": reference["source_kind"],
            "source_projection": _reference_source_projection(payload, reference),
            "source_state": reference["source_state"],
            "target_base_revision": reference["target_base_revision"] or "",
            "target_generation": reference["target_generation"],
            "target_kind": reference["target_kind"],
            "target_value": reference["target_value"],
            "task_id": payload["task"]["task_id"],
            "verification_criterion_id": reference["verification_criterion_id"],
        }
        reference["digest"] = domain_digest(EVIDENCE_REFERENCE_DOMAIN, value)


def refresh_bundle_seals(payload: dict[str, Any]) -> None:
    """Refresh test-owned provenance and inner Bundle digests in place."""

    target = deepcopy(payload["target"])
    target["base_revision"] = target["base_revision"] or ""
    for receipt in payload["review_receipts"]:
        provenance = receipt["review_provenance"]
        if provenance is None:
            continue
        digest_payload = {
            "project_id": payload["project_id"],
            "task_id": payload["task"]["task_id"],
            "review_receipt_id": receipt["review_receipt_id"],
            "receipt_kind": receipt["receipt_kind"],
            "target": target,
            **{
                key: provenance[key]
                for key in (
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
                )
            },
        }
        provenance["digest"] = domain_digest(
            REVIEW_PROVENANCE_DOMAIN,
            digest_payload,
        )
    refresh_inner_digests(payload)


def valid_native_payload() -> dict[str, object]:
    """Return one literal representative native Bundle payload."""

    payload = deepcopy(sample_payload())
    payload["criteria"] = sorted(
        payload["criteria"],
        key=lambda item: ({"acceptance": 0, "verification": 1}[item["kind"]], item["criterion_id"]),
    )
    for criterion in payload["criteria"]:
        if criterion["kind"] == "acceptance":
            criterion["text"] = payload["contract"]["acceptance"]
    criterion_kind = {
        item["criterion_id"]: item["kind"] for item in payload["criteria"]
    }
    payload["criterion_links"] = sorted(
        payload["criterion_links"],
        key=lambda item: (
            {"acceptance": 0, "verification": 1}[criterion_kind[item["criterion_id"]]],
            item["criterion_id"],
            {"review_assessment": 0, "review_finding": 1, "completion_basis": 2, "verification_attestation": 3}[item["relation"]],
            item["evidence_reference_id"],
            item["criterion_evidence_link_id"],
        ),
    )
    source_order = {
        "artifact_manifest": 0,
        "verification_receipt": 1,
        "review_receipt": 2,
        "review_finding": 3,
        "completion_evidence": 4,
    }
    payload["evidence_references"] = sorted(
        payload["evidence_references"],
        key=lambda item: (
            source_order[item["source_kind"]],
            item["source_id"],
            item["evidence_reference_id"],
        ),
    )
    payload["target"]["base_revision"] = None
    for reference in payload["evidence_references"]:
        reference["target_base_revision"] = None
    payload["completion_evidence"]["reason"] = ""
    payload["task"]["review_tier"] = 0
    payload["review_receipts"] = [
        {
            "review_receipt_id": "tg_review_receipt_0123456789abcdef",
            "reviewer_key": "tier-zero-not-required",
            "receipt_kind": "not_required",
            "verdict": "not_required",
            "summary": "No independent review is required for this fixture.",
            "user_approved": 0,
            "created_at": "2026-08-05T00:01:00Z",
            "review_provenance": None,
        }
    ]
    acceptance_id = next(
        item["criterion_id"]
        for item in payload["criteria"]
        if item["kind"] == "acceptance"
    )
    review_reference_id = "tg_evidence_reference_7777777777777777"
    completion_reference_id = "tg_evidence_reference_8888888888888888"

    def reference(
        *,
        reference_id: str,
        source_kind: str,
        source_state: str,
        source_id: str,
        completion_cycle_id: str | None,
        digest_hex: str,
    ) -> dict[str, object]:
        return {
            "evidence_reference_id": reference_id,
            "source_kind": source_kind,
            "source_state": source_state,
            "source_id": source_id,
            "assurance_class": "bound_attestation",
            "producer_class": "trusted_caller",
            "producer_version": 1,
            "contract_revision": payload["contract"]["revision"],
            "authority_snapshot_id": payload["authority_snapshot"][
                "authority_snapshot_id"
            ],
            "acceptance_criterion_id": acceptance_id,
            "verification_criterion_id": next(
                item["criterion_id"]
                for item in payload["criteria"]
                if item["kind"] == "verification"
            ),
            "target_kind": payload["target"]["kind"],
            "target_value": payload["target"]["value"],
            "target_base_revision": None,
            "target_generation": payload["target"]["generation"],
            "completion_cycle_id": completion_cycle_id,
            "digest": "sha256:" + (digest_hex * 64),
        }

    payload["evidence_references"].extend(
        (
            reference(
                reference_id=review_reference_id,
                source_kind="review_receipt",
                source_state="recorded",
                source_id=payload["review_receipts"][0]["review_receipt_id"],
                completion_cycle_id=None,
                digest_hex="1",
            ),
            reference(
                reference_id=completion_reference_id,
                source_kind="completion_evidence",
                source_state="commit_not_required",
                source_id=payload["completion_cycle_id"],
                completion_cycle_id=payload["completion_cycle_id"],
                digest_hex="2",
            ),
        )
    )
    payload["criterion_links"].extend(
        (
            {
                "criterion_evidence_link_id": (
                    "tg_criterion_evidence_link_" + ("c" * 16)
                ),
                "criterion_id": acceptance_id,
                "evidence_reference_id": review_reference_id,
                "relation": "review_assessment",
                "assurance_class": "bound_attestation",
                "producer_class": "trusted_caller",
                "producer_version": 1,
            },
            {
                "criterion_evidence_link_id": (
                    "tg_criterion_evidence_link_" + ("d" * 16)
                ),
                "criterion_id": acceptance_id,
                "evidence_reference_id": completion_reference_id,
                "relation": "completion_basis",
                "assurance_class": "bound_attestation",
                "producer_class": "trusted_caller",
                "producer_version": 1,
            },
        )
    )
    payload["criterion_links"] = sorted(
        payload["criterion_links"],
        key=lambda item: (
            {"acceptance": 0, "verification": 1}[
                criterion_kind[item["criterion_id"]]
            ],
            item["criterion_id"],
            {
                "review_assessment": 0,
                "review_finding": 1,
                "completion_basis": 2,
                "verification_attestation": 3,
            }[item["relation"]],
            item["evidence_reference_id"],
            item["criterion_evidence_link_id"],
        ),
    )
    payload["evidence_references"] = sorted(
        payload["evidence_references"],
        key=lambda item: (
            source_order[item["source_kind"]],
            item["source_id"],
            item["evidence_reference_id"],
        ),
    )
    refresh_inner_digests(payload)
    return payload


def v1_native_payload(
    *,
    reviewer_class: str = "human",
    model_state: str = "not_applicable",
    declared_model_id: str | None = None,
    skill_state: str = "not_applicable",
    declared_skill_id: str | None = None,
    declared_skill_version: str | None = None,
    context_relation: str = "not_applicable",
) -> dict[str, object]:
    """Return one literal native Bundle payload with v1 review provenance."""

    payload = valid_native_payload()
    receipt = payload["review_receipts"][0]
    receipt.update(
        {
            "reviewer_key": "fixture-reviewer",
            "receipt_kind": "independent",
            "verdict": "pass",
            "summary": "No blocking findings.",
            "review_provenance": {
                "review_provenance_id": "tg_review_provenance_" + "9" * 16,
                "provenance_version": 1,
                "reviewer_class": reviewer_class,
                "model_state": model_state,
                "declared_model_id": declared_model_id,
                "skill_state": skill_state,
                "declared_skill_id": declared_skill_id,
                "declared_skill_version": declared_skill_version,
                "review_profiles": ["general"],
                "review_lenses": ["correctness"],
                "context_relation": context_relation,
                "method_codes": ["review_packet_inspection"],
                "assurance_class": "bound_attestation",
                "producer_class": "trusted_caller",
                "producer_version": 1,
                "digest": "sha256:" + "9" * 64,
            },
        }
    )
    payload["task"]["review_tier"] = 1
    return payload


def reidentify_native_payload(
    payload: dict[str, object],
    *,
    suffix: str,
    cycle_ordinal: int,
) -> dict[str, object]:
    """Give a one-Receipt test payload distinct cycle and provenance IDs."""

    if (
        len(suffix) != 1
        or suffix not in "0123456789abcdef"
        or type(cycle_ordinal) is not int
        or not 1 <= cycle_ordinal <= 99
        or len(payload["review_receipts"]) != 1
    ):
        raise ValueError("invalid M23 native fixture identity")
    selected = deepcopy(payload)
    old_cycle_id = selected["completion_cycle_id"]
    old_receipt_id = selected["review_receipts"][0]["review_receipt_id"]
    cycle_id = "tg_completion_cycle_" + suffix * 16
    receipt_id = "tg_review_receipt_" + suffix * 16
    selected.update(
        {
            "bundle_id": "tg_completion_evidence_bundle_" + suffix * 16,
            "completion_cycle_id": cycle_id,
            "cycle_ordinal": cycle_ordinal,
            "sealed_at": f"2026-08-05T00:{cycle_ordinal:02d}:00Z",
        }
    )
    receipt = selected["review_receipts"][0]
    receipt["review_receipt_id"] = receipt_id
    provenance = receipt["review_provenance"]
    if provenance is not None:
        provenance["review_provenance_id"] = (
            "tg_review_provenance_" + suffix * 16
        )
    for reference in selected["evidence_references"]:
        if reference["source_kind"] == "review_receipt":
            if reference["source_id"] != old_receipt_id:
                raise ValueError("invalid M23 review fixture binding")
            reference["source_id"] = receipt_id
        if reference["source_kind"] == "completion_evidence":
            if reference["source_id"] != old_cycle_id:
                raise ValueError("invalid M23 completion fixture binding")
            reference["source_id"] = cycle_id
        if reference["completion_cycle_id"] == old_cycle_id:
            reference["completion_cycle_id"] = cycle_id
    refresh_bundle_seals(selected)
    return selected


def sealed_bundle(payload: dict[str, object] | None = None) -> tuple[dict, bytes]:
    selected = valid_native_payload() if payload is None else deepcopy(payload)
    digest = domain_digest(BUNDLE_DOMAIN, selected)
    envelope = {
        "bundle_digest": digest,
        "format_version": 1,
        "payload": selected,
    }
    return envelope, reference_json_bytes(envelope) + b"\n"


def index_entries(bundle_document: bytes, bundle_digest: str) -> list[dict]:
    payload = valid_native_payload()
    native = {
        "task_id": payload["task"]["task_id"],
        "completion_cycle_id": payload["completion_cycle_id"],
        "cycle_ordinal": payload["cycle_ordinal"],
        "bundle_state": "native",
        "bundle_id": payload["bundle_id"],
        "bundle_file": f"bundles/{payload['bundle_id']}.json",
        "bundle_digest": bundle_digest,
        "file_digest": "sha256:" + hashlib.sha256(bundle_document).hexdigest(),
        "sealed_at": payload["sealed_at"],
    }
    legacy = {
        "task_id": "tg_task_0000000000000000",
        "completion_cycle_id": "tg_completion_cycle_0000000000000000",
        "cycle_ordinal": 1,
        "bundle_state": "legacy_unknown",
        "bundle_id": None,
        "bundle_file": None,
        "bundle_digest": None,
        "file_digest": None,
        "sealed_at": None,
    }
    return sorted(
        (native, legacy),
        key=lambda item: (
            item["task_id"].encode("utf-8"),
            item["cycle_ordinal"],
            item["completion_cycle_id"].encode("utf-8"),
        ),
    )


def write_evidence_tree(root: Path) -> Path:
    evidence = root / "evidence"
    bundles = evidence / "bundles"
    bundles.mkdir(parents=True)
    bundle, bundle_document = sealed_bundle()
    bundle_id = bundle["payload"]["bundle_id"]
    (bundles / f"{bundle_id}.json").write_bytes(bundle_document)
    entries = index_entries(bundle_document, bundle["bundle_digest"])
    payload = {
        "source_schema_version": 19,
        "project_id": bundle["payload"]["project_id"],
        "projection_generation": 7,
        "bundle_count": 1,
        "legacy_count": 1,
        "entries": entries,
    }
    envelope = {
        "format_version": 1,
        "index_digest": domain_digest(INDEX_DOMAIN, payload),
        "payload": payload,
    }
    (evidence / "index.json").write_bytes(reference_json_bytes(envelope) + b"\n")
    return evidence


def write_mixed_evidence_tree(root: Path) -> Path:
    """Write three native cycles and two index-only legacy entries."""

    evidence = root / "evidence"
    bundles = evidence / "bundles"
    bundles.mkdir(parents=True)
    native_payloads = (
        reidentify_native_payload(
            v1_native_payload(),
            suffix="a",
            cycle_ordinal=2,
        ),
        reidentify_native_payload(
            v1_native_payload(
                reviewer_class="llm",
                model_state="declared",
                declared_model_id="fixture-model-b",
                skill_state="declared",
                declared_skill_id="fixture-skill-b",
                declared_skill_version="1.0",
                context_relation="forked_context",
            ),
            suffix="b",
            cycle_ordinal=3,
        ),
        reidentify_native_payload(
            valid_native_payload(),
            suffix="c",
            cycle_ordinal=4,
        ),
    )
    entries: list[dict[str, object]] = []
    for payload in native_payloads:
        bundle, document = sealed_bundle(payload)
        bundle_id = payload["bundle_id"]
        (bundles / f"{bundle_id}.json").write_bytes(document)
        entries.append(
            {
                "task_id": payload["task"]["task_id"],
                "completion_cycle_id": payload["completion_cycle_id"],
                "cycle_ordinal": payload["cycle_ordinal"],
                "bundle_state": "native",
                "bundle_id": bundle_id,
                "bundle_file": f"bundles/{bundle_id}.json",
                "bundle_digest": bundle["bundle_digest"],
                "file_digest": "sha256:" + hashlib.sha256(document).hexdigest(),
                "sealed_at": payload["sealed_at"],
            }
        )
    for suffix in ("d", "e"):
        entries.append(
            {
                "task_id": "tg_task_" + suffix * 16,
                "completion_cycle_id": "tg_completion_cycle_" + suffix * 16,
                "cycle_ordinal": 1,
                "bundle_state": "legacy_unknown",
                "bundle_id": None,
                "bundle_file": None,
                "bundle_digest": None,
                "file_digest": None,
                "sealed_at": None,
            }
        )
    entries.sort(
        key=lambda item: (
            item["task_id"].encode("utf-8"),
            item["cycle_ordinal"],
            item["completion_cycle_id"].encode("utf-8"),
        )
    )
    payload = {
        "source_schema_version": 19,
        "project_id": native_payloads[0]["project_id"],
        "projection_generation": 7,
        "bundle_count": len(native_payloads),
        "legacy_count": 2,
        "entries": entries,
    }
    envelope = {
        "format_version": 1,
        "index_digest": domain_digest(INDEX_DOMAIN, payload),
        "payload": payload,
    }
    (evidence / "index.json").write_bytes(reference_json_bytes(envelope) + b"\n")
    return evidence
