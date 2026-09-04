from __future__ import annotations

import hashlib
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    import task_governance_tool.evidence_projection as projection
    from task_governance_tool.evidence_projection import (
        BUNDLE_DOMAIN,
        FINDING_SNAPSHOT_DOMAIN,
        INDEX_DOMAIN,
        EvidenceProjectionError,
        build_bundle_artifact,
        build_index_artifact,
        build_native_bundle_plan,
        required_native_bundle_link_count,
    )
finally:
    sys.path.pop(0)


DIGEST_A = "sha256:" + ("a" * 64)
DIGEST_B = "sha256:" + ("b" * 64)
DIGEST_C = "sha256:" + ("c" * 64)
BUNDLE_ID = "tg_completion_evidence_bundle_" + ("1" * 16)
CYCLE_ID = "tg_completion_cycle_" + ("2" * 16)


def reference_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sample_payload() -> dict[str, object]:
    acceptance_id = "tg_contract_criterion_" + ("3" * 16)
    verification_id = "tg_contract_criterion_" + ("4" * 16)
    manifest_reference = "tg_evidence_reference_" + ("5" * 16)
    verification_reference = "tg_evidence_reference_" + ("6" * 16)
    return {
        "artifact_manifest": {
            "artifact_manifest_id": "tg_artifact_manifest_" + ("7" * 16),
            "state": "complete_git",
            "object_format": "sha1",
            "comparison_base": "8" * 40,
            "digest": DIGEST_A,
            "omission_code": None,
            "entries": [],
        },
        "authority_snapshot": {
            "authority_snapshot_id": "tg_authority_snapshot_" + ("9" * 16),
            "generation": 3,
            "digest": DIGEST_B,
        },
        "bundle_id": BUNDLE_ID,
        "bundle_version": 1,
        "completion_cycle_id": CYCLE_ID,
        "cycle_ordinal": 2,
        "sealed_at": "2026-08-05T00:00:00Z",
        "completion_evidence": {
            "kind": "commit_not_required",
            "revision": "",
            "reason": "Documentation-only completion",
            "external_revision_approved": 0,
            "completion_commit_required": 0,
            "completion_commit_hash": "",
        },
        "contract": {
            "revision": 10,
            "specified": True,
            "scope": "scope",
            "acceptance": "acceptance",
            "constraints": "constraints",
            "authority_ref": "authority",
        },
        "criteria": [
            {
                "criterion_id": verification_id,
                "kind": "verification",
                "text": "verify",
                "digest": DIGEST_C,
            },
            {
                "criterion_id": acceptance_id,
                "kind": "acceptance",
                "text": "accept",
                "digest": DIGEST_B,
            },
        ],
        "criterion_links": [
            {
                "criterion_evidence_link_id": (
                    "tg_criterion_evidence_link_" + ("a" * 16)
                ),
                "criterion_id": verification_id,
                "evidence_reference_id": verification_reference,
                "relation": "verification_attestation",
                "assurance_class": "bound_attestation",
                "producer_class": "trusted_caller",
                "producer_version": 1,
            },
            {
                "criterion_evidence_link_id": (
                    "tg_criterion_evidence_link_" + ("b" * 16)
                ),
                "criterion_id": acceptance_id,
                "evidence_reference_id": manifest_reference,
                "relation": "completion_basis",
                "assurance_class": "machine_observed",
                "producer_class": "taskgov_git",
                "producer_version": 1,
            },
        ],
        "evidence_references": [
            {
                "evidence_reference_id": verification_reference,
                "source_kind": "verification_receipt",
                "source_state": "recorded",
                "source_id": "tg_verification_receipt_" + ("c" * 16),
                "assurance_class": "bound_attestation",
                "producer_class": "trusted_caller",
                "producer_version": 1,
                "contract_revision": 10,
                "authority_snapshot_id": (
                    "tg_authority_snapshot_" + ("9" * 16)
                ),
                "acceptance_criterion_id": acceptance_id,
                "verification_criterion_id": verification_id,
                "target_kind": "git_commit",
                "target_value": "d" * 40,
                "target_base_revision": "",
                "target_generation": 4,
                "completion_cycle_id": None,
                "digest": DIGEST_C,
            },
            {
                "evidence_reference_id": manifest_reference,
                "source_kind": "artifact_manifest",
                "source_state": "complete_git",
                "source_id": "tg_artifact_manifest_" + ("7" * 16),
                "assurance_class": "machine_observed",
                "producer_class": "taskgov_git",
                "producer_version": 1,
                "contract_revision": 10,
                "authority_snapshot_id": (
                    "tg_authority_snapshot_" + ("9" * 16)
                ),
                "acceptance_criterion_id": acceptance_id,
                "verification_criterion_id": verification_id,
                "target_kind": "git_commit",
                "target_value": "d" * 40,
                "target_base_revision": "",
                "target_generation": 4,
                "completion_cycle_id": None,
                "digest": DIGEST_A,
            },
        ],
        "finding_snapshots": [],
        "omissions": [],
        "project_id": "tg_project_" + ("e" * 32),
        "review_receipts": [],
        "source_schema_version": 19,
        "target": {
            "kind": "git_commit",
            "value": "d" * 40,
            "base_revision": "",
            "generation": 4,
            "capture_version": 1,
        },
        "task": {
            "task_id": "tg_task_" + ("f" * 16),
            "title": "Task",
            "description": "Description",
            "review_tier": 2,
            "verification": "verify",
        },
        "verification_receipt": {
            "verification_receipt_id": (
                "tg_verification_receipt_" + ("c" * 16)
            ),
            "verification_subject": {
                "basis_version": 1,
                "kind": "task_verification_criterion",
                "authority_snapshot_id": (
                    "tg_authority_snapshot_" + ("9" * 16)
                ),
                "verification_criterion_id": verification_id,
            },
            "result": "pass",
            "duration_ms": 12,
            "scope_coverage": "full",
            "created_at": "2026-08-04T00:00:00Z",
        },
    }


class BundleAssemblyPureTests(unittest.TestCase):
    def test_native_bundle_plan_builds_one_representative_link_matrix(self) -> None:
        payload = sample_payload()
        criteria_by_kind = {
            item["kind"]: item for item in payload["criteria"]
        }
        acceptance_id = criteria_by_kind["acceptance"]["criterion_id"]
        verification_id = criteria_by_kind["verification"]["criterion_id"]
        references_by_kind = {
            item["source_kind"]: item
            for item in payload["evidence_references"]
        }
        artifact_reference = dict(references_by_kind["artifact_manifest"])
        verification_reference = dict(
            references_by_kind["verification_receipt"]
        )

        review_receipt_id = "tg_review_receipt_" + ("7" * 16)
        review_reference_id = "tg_evidence_reference_" + ("7" * 16)
        finding_id = "tg_review_finding_" + ("8" * 16)
        finding_reference_id = "tg_evidence_reference_" + ("8" * 16)
        completion_reference_id = "tg_evidence_reference_" + ("9" * 16)

        def reference(
            *,
            reference_id: str,
            source_kind: str,
            source_state: str,
            source_id: str,
            assurance_class: str,
            producer_class: str,
            completion_cycle_id: str | None = None,
        ) -> dict[str, object]:
            return {
                "evidence_reference_id": reference_id,
                "source_kind": source_kind,
                "source_state": source_state,
                "source_id": source_id,
                "assurance_class": assurance_class,
                "producer_class": producer_class,
                "producer_version": 1,
                "contract_revision": 10,
                "authority_snapshot_id": (
                    payload["authority_snapshot"]["authority_snapshot_id"]
                ),
                "acceptance_criterion_id": acceptance_id,
                "verification_criterion_id": verification_id,
                "target_kind": payload["target"]["kind"],
                "target_value": payload["target"]["value"],
                "target_base_revision": payload["target"]["base_revision"],
                "target_generation": payload["target"]["generation"],
                "completion_cycle_id": completion_cycle_id,
                "digest": DIGEST_C,
            }

        review_reference = reference(
            reference_id=review_reference_id,
            source_kind="review_receipt",
            source_state="recorded",
            source_id=review_receipt_id,
            assurance_class="bound_attestation",
            producer_class="trusted_caller",
        )
        finding_reference = reference(
            reference_id=finding_reference_id,
            source_kind="review_finding",
            source_state="recorded",
            source_id=finding_id,
            assurance_class="bound_attestation",
            producer_class="trusted_caller",
        )
        completion_reference = reference(
            reference_id=completion_reference_id,
            source_kind="completion_evidence",
            source_state="commit_not_required",
            source_id=CYCLE_ID,
            assurance_class="bound_attestation",
            producer_class="trusted_caller",
            completion_cycle_id=CYCLE_ID,
        )
        basis = SimpleNamespace(
            source_schema_version=21,
            task=dict(payload["task"]),
            authority_snapshot={
                "authority_snapshot_id": payload["authority_snapshot"][
                    "authority_snapshot_id"
                ],
                "generation": payload["authority_snapshot"]["generation"],
                "basis_digest": payload["authority_snapshot"]["digest"],
                "contract_revision": payload["contract"]["revision"],
                "contract_state": "contract_specified",
                "contract_scope": payload["contract"]["scope"],
                "contract_acceptance": payload["contract"]["acceptance"],
                "contract_constraints": payload["contract"]["constraints"],
                "contract_authority_ref": payload["contract"]["authority_ref"],
            },
            criteria=tuple(
                {
                    "criterion_id": item["criterion_id"],
                    "criterion_kind": item["kind"],
                    "criterion_text": item["text"],
                    "digest": item["digest"],
                }
                for item in payload["criteria"]
            ),
            artifact_manifest={
                key: value
                for key, value in payload["artifact_manifest"].items()
                if key != "entries"
            },
            artifact_entries=tuple(
                {
                    **entry,
                    "entry_kind": entry["kind"],
                }
                for entry in payload["artifact_manifest"]["entries"]
            ),
            artifact_reference=artifact_reference,
            verification_receipt=dict(payload["verification_receipt"]),
            verification_reference=verification_reference,
            review_receipts=(
                {
                    "receipt": {
                        "review_receipt_id": review_receipt_id,
                        "reviewer_key": "representative-reviewer",
                        "receipt_kind": "independent",
                        "verdict": "pass",
                        "summary": "representative pass",
                        "user_approved": 0,
                        "created_at": "2026-08-04T01:00:00Z",
                    },
                    "provenance": {"provenance_version": 1},
                },
            ),
            review_references=(review_reference,),
            findings=(
                {
                    "review_finding_id": finding_id,
                    "review_receipt_id": review_receipt_id,
                    "target_generation": 4,
                    "severity": "low",
                    "summary": "representative current finding",
                    "status": "open",
                    "resolution_summary": "",
                    "created_at": "2026-08-04T02:00:00Z",
                    "resolved_at": None,
                },
            ),
            finding_references=(finding_reference,),
            completion_reference=completion_reference,
        )
        cycle = SimpleNamespace(
            project_id=payload["project_id"],
            task_id=payload["task"]["task_id"],
            review_target_kind=payload["target"]["kind"],
            review_target_value=payload["target"]["value"],
            review_target_base_revision=payload["target"]["base_revision"],
            review_target_generation=payload["target"]["generation"],
            completion_evidence_kind=payload["completion_evidence"]["kind"],
            completion_evidence_revision=payload["completion_evidence"][
                "revision"
            ],
            completion_evidence_reason=payload["completion_evidence"]["reason"],
            external_revision_approved=False,
            completion_commit_required=False,
            completion_commit_hash="",
            verification_basis_kind="caller_attestation",
            verification_receipt_id=payload["verification_receipt"][
                "verification_receipt_id"
            ],
        )
        completion_identity = SimpleNamespace(
            completion_evidence_bundle_id=BUNDLE_ID,
            completion_cycle_id=CYCLE_ID,
            saved_cycle_ordinal=2,
        )
        link_ids = tuple(
            "tg_criterion_evidence_link_" + (value * 16)
            for value in "abcde"
        )

        self.assertEqual(
            required_native_bundle_link_count(basis=basis, cycle=cycle),
            5,
        )
        plan = build_native_bundle_plan(
            basis=basis,
            cycle=cycle,
            completion_identity=completion_identity,
            criterion_link_ids=link_ids,
            sealed_at="2026-08-05T00:00:00Z",
        )

        self.assertIsNone(plan.artifact.payload["target"]["base_revision"])
        self.assertTrue(
            all(
                item["target_base_revision"] is None
                for item in plan.artifact.payload["evidence_references"]
            )
        )
        self.assertEqual(
            tuple(
                (
                    ordinal,
                    link["relation"],
                    link["evidence_reference_id"],
                )
                for ordinal, link in enumerate(plan.storage_links)
            ),
            (
                (0, "review_assessment", review_reference_id),
                (1, "review_finding", finding_reference_id),
                (
                    2,
                    "completion_basis",
                    artifact_reference["evidence_reference_id"],
                ),
                (3, "completion_basis", completion_reference_id),
                (
                    4,
                    "verification_attestation",
                    verification_reference["evidence_reference_id"],
                ),
            ),
        )
        self.assertEqual(
            tuple(enumerate(plan.reference_ids)),
            (
                (0, artifact_reference["evidence_reference_id"]),
                (1, verification_reference["evidence_reference_id"]),
                (2, review_reference_id),
                (3, finding_reference_id),
                (4, completion_reference_id),
            ),
        )
        self.assertEqual(
            tuple(
                (ordinal, item["review_finding_id"])
                for ordinal, item in enumerate(plan.finding_snapshots)
            ),
            ((0, finding_id),),
        )

    def test_bundle_orders_arrays_and_uses_independent_digest_oracle(self) -> None:
        artifact = build_bundle_artifact(sample_payload())
        self.assertIsNone(artifact.payload["target"]["base_revision"])
        self.assertTrue(
            all(
                item["target_base_revision"] is None
                for item in artifact.payload["evidence_references"]
            )
        )
        self.assertEqual(
            [item["kind"] for item in artifact.payload["criteria"]],
            ["acceptance", "verification"],
        )
        self.assertEqual(
            [
                item["source_kind"]
                for item in artifact.payload["evidence_references"]
            ],
            ["artifact_manifest", "verification_receipt"],
        )
        expected_payload = reference_json_bytes(artifact.payload)
        expected_digest = "sha256:" + hashlib.sha256(
            BUNDLE_DOMAIN + expected_payload
        ).hexdigest()
        self.assertEqual(artifact.payload_bytes, expected_payload)
        self.assertEqual(artifact.bundle_digest, expected_digest)
        expected_envelope = {
            "bundle_digest": expected_digest,
            "format_version": 1,
            "payload": artifact.payload,
        }
        expected_document = reference_json_bytes(expected_envelope) + b"\n"
        self.assertEqual(artifact.document, expected_document)
        self.assertEqual(
            artifact.file_digest,
            "sha256:" + hashlib.sha256(expected_document).hexdigest(),
        )

    def test_finding_digest_and_fixed_omission_order(self) -> None:
        payload = sample_payload()
        finding = {
            "review_finding_id": "tg_review_finding_" + ("1" * 16),
            "review_receipt_id": "tg_review_receipt_" + ("2" * 16),
            "target_generation": 1,
            "severity": "medium",
            "summary": "fixed",
            "status": "resolved",
            "resolution_summary": "resolved",
            "created_at": "2026-08-01T00:00:00Z",
            "resolved_at": "2026-08-02T00:00:00Z",
            "evidence_reference_id": None,
            "assurance_class": "legacy_unknown",
            "producer_class": "legacy_migration",
            "producer_version": 1,
        }
        expected_finding_digest = "sha256:" + hashlib.sha256(
            FINDING_SNAPSHOT_DOMAIN + reference_json_bytes(finding)
        ).hexdigest()
        finding["digest"] = expected_finding_digest
        payload["finding_snapshots"] = [finding]
        payload["omissions"] = [
            "historical_finding_reference_absent",
            "acceptance_criterion_absent",
        ]
        artifact = build_bundle_artifact(payload)
        self.assertEqual(
            artifact.payload["omissions"],
            [
                "acceptance_criterion_absent",
                "historical_finding_reference_absent",
            ],
        )
        self.assertEqual(
            artifact.payload["finding_snapshots"][0]["digest"],
            expected_finding_digest,
        )

    def test_rejects_legacy_subject_and_provenance_v0(self) -> None:
        legacy_subject = sample_payload()
        legacy_subject["verification_receipt"][
            "verification_subject"
        ]["legacy_caller_label"] = "legacy"
        with self.assertRaises(EvidenceProjectionError):
            build_bundle_artifact(legacy_subject)

        legacy_review = sample_payload()
        legacy_review["review_receipts"] = [
            {
                "review_receipt_id": "tg_review_receipt_" + ("4" * 16),
                "reviewer_key": "reviewer",
                "receipt_kind": "independent",
                "verdict": "pass",
                "summary": "pass",
                "user_approved": 0,
                "created_at": "2026-08-04T00:00:00Z",
                "review_provenance": {"provenance_version": 0},
            }
        ]
        with self.assertRaises(EvidenceProjectionError):
            build_bundle_artifact(legacy_review)

    def test_bundle_size_failure_is_closed(self) -> None:
        with patch.object(projection, "BUNDLE_MAX_BYTES", 10):
            with self.assertRaises(EvidenceProjectionError) as raised:
                build_bundle_artifact(sample_payload())
        self.assertEqual(
            raised.exception.code,
            "evidence_bundle_too_large",
        )

    def test_index_native_and_legacy_entries_are_sorted_and_sealed(self) -> None:
        bundle = build_bundle_artifact(sample_payload())
        basis = {
            "source_schema_version": 19,
            "project_id": "tg_project_" + ("e" * 32),
            "projection_generation": 7,
            "bundle_count": 1,
            "legacy_count": 1,
            "entries": [
                {
                    "task_id": "tg_task_" + ("f" * 16),
                    "completion_cycle_id": CYCLE_ID,
                    "cycle_ordinal": 2,
                    "bundle_state": "native",
                    "bundle_id": BUNDLE_ID,
                    "bundle_file": f"bundles/{BUNDLE_ID}.json",
                    "bundle_digest": bundle.bundle_digest,
                    "file_digest": bundle.file_digest,
                    "sealed_at": "2026-08-05T00:00:00Z",
                },
                {
                    "task_id": "tg_task_" + ("0" * 16),
                    "completion_cycle_id": (
                        "tg_completion_cycle_" + ("0" * 16)
                    ),
                    "cycle_ordinal": 1,
                    "bundle_state": "legacy_unknown",
                    "bundle_id": None,
                    "bundle_file": None,
                    "bundle_digest": None,
                    "file_digest": None,
                    "sealed_at": None,
                },
            ],
        }
        artifact = build_index_artifact(basis)
        self.assertEqual(
            artifact.payload["entries"][0]["bundle_state"],
            "legacy_unknown",
        )
        expected_payload = reference_json_bytes(artifact.payload)
        expected_digest = "sha256:" + hashlib.sha256(
            INDEX_DOMAIN + expected_payload
        ).hexdigest()
        self.assertEqual(artifact.index_digest, expected_digest)
        self.assertEqual(
            artifact.document,
            reference_json_bytes(
                {
                    "format_version": 1,
                    "index_digest": expected_digest,
                    "payload": artifact.payload,
                }
            )
            + b"\n",
        )


if __name__ == "__main__":
    unittest.main()
