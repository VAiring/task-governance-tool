from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool import storage
from task_governance_tool.verification_runner import (
    resolution_idempotency_digest,
    verification_runner_attempt_digest,
)


NOW = "2026-08-21T00:00:00Z"

FAIL_STAGES = (
    "after_columns",
    "after_bundle",
    "after_runner_tables",
    "after_objects",
    "after_marker",
    "before_commit",
)

V19_TABLES = (
    "artifact_manifest_entries",
    "artifact_manifests",
    "authority_snapshot_criteria",
    "authority_snapshots",
    "completion_bundle_finding_snapshots",
    "completion_bundle_members",
    "completion_evidence_bundles",
    "contract_criteria",
    "criterion_evidence_links",
    "evidence_projection_state",
    "evidence_references",
    "handoff_records",
    "managed_backup_generations",
    "project_maintenance",
    "project_meta",
    "project_path_binding_history",
    "review_findings",
    "review_receipt_provenance",
    "review_receipt_provenance_codes",
    "review_receipts",
    "schema_migrations",
    "task_checkpoints",
    "task_completion_cycles",
    "task_contract_revisions",
    "task_effort_activity",
    "task_effort_bases",
    "task_events",
    "tasks",
    "tool_events",
    "verification_receipts",
    "viewer_maintenance_state",
)
V19_BUSINESS_TABLES = tuple(
    name for name in V19_TABLES if name != "schema_migrations"
)
TASK_R3A_COLUMN_SQL = (
    "review_target_runner_basis_version INTEGER NOT NULL DEFAULT 0 "
    "CHECK (review_target_runner_basis_version IN (0, 2))"
)
CYCLE_R3A_COLUMN_SQL = (
    "verification_basis_kind TEXT",
    "verification_runner_observation_id TEXT",
)

# Test-owned Step2E matrix.  The expected values are intentionally independent
# of all production DDL builders and schema-expectation helpers.
RUNNER_TABLE_XINFO = {
    "verification_runner_resolutions": (
        ("verification_runner_resolution_id", "TEXT", 0, None, 1),
        ("project_id", "TEXT", 1, None, 0),
        ("task_id", "TEXT", 1, None, 0),
        ("contract_revision", "INTEGER", 1, None, 0),
        ("authority_snapshot_id", "TEXT", 1, None, 0),
        ("verification_criterion_id", "TEXT", 1, None, 0),
        ("verification_expectation_digest", "TEXT", 1, None, 0),
        ("verification_criterion_digest", "TEXT", 1, None, 0),
        ("target_kind", "TEXT", 1, None, 0),
        ("target_value", "TEXT", 1, None, 0),
        ("target_base_revision", "TEXT", 0, None, 0),
        ("target_generation", "INTEGER", 1, None, 0),
        ("target_capture_version", "INTEGER", 1, None, 0),
        ("artifact_manifest_id", "TEXT", 1, None, 0),
        ("target_material_digest", "TEXT", 0, None, 0),
        ("plan_state", "TEXT", 1, None, 0),
        ("plan_blob_object_id", "TEXT", 0, None, 0),
        ("plan_raw_digest", "TEXT", 0, None, 0),
        ("plan_id", "TEXT", 0, None, 0),
        ("plan_version", "INTEGER", 0, None, 0),
        ("plan_semantic_digest", "TEXT", 0, None, 0),
        ("selected_entry_digest", "TEXT", 0, None, 0),
        ("coverage", "TEXT", 1, None, 0),
        ("step_count", "INTEGER", 1, None, 0),
        ("runner_contract_version", "INTEGER", 1, None, 0),
        ("runner_implementation_version", "TEXT", 1, None, 0),
        ("runner_implementation_digest", "TEXT", 1, None, 0),
        ("runner_policy_digest", "TEXT", 1, None, 0),
        ("runtime_digest", "TEXT", 0, None, 0),
        ("gate_eligibility_version", "INTEGER", 1, None, 0),
        ("trigger", "TEXT", 1, None, 0),
        ("route", "TEXT", 1, None, 0),
        ("reason", "TEXT", 0, None, 0),
        ("idempotency_digest", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
    ),
    "verification_runner_attempts": (
        ("verification_runner_attempt_id", "TEXT", 0, None, 1),
        ("project_id", "TEXT", 1, None, 0),
        ("task_id", "TEXT", 1, None, 0),
        ("target_generation", "INTEGER", 1, None, 0),
        ("gate_eligibility_version", "INTEGER", 1, None, 0),
        ("verification_runner_resolution_id", "TEXT", 1, None, 0),
        ("target_material_digest", "TEXT", 1, None, 0),
        ("runner_implementation_digest", "TEXT", 1, None, 0),
        ("attempt_digest", "TEXT", 1, None, 0),
        ("intent_recorded_at", "TEXT", 1, None, 0),
    ),
    "verification_runner_sandbox_events": (
        ("verification_runner_sandbox_event_id", "TEXT", 0, None, 1),
        ("project_id", "TEXT", 1, None, 0),
        ("task_id", "TEXT", 1, None, 0),
        ("target_generation", "INTEGER", 1, None, 0),
        ("verification_runner_attempt_id", "TEXT", 1, None, 0),
        ("event_kind", "TEXT", 1, None, 0),
        ("event_digest", "TEXT", 1, None, 0),
        ("terminal_observation_id", "TEXT", 0, None, 0),
        ("created_at", "TEXT", 1, None, 0),
    ),
    "verification_runner_observations": (
        ("verification_runner_observation_id", "TEXT", 0, None, 1),
        ("project_id", "TEXT", 1, None, 0),
        ("task_id", "TEXT", 1, None, 0),
        ("target_generation", "INTEGER", 1, None, 0),
        ("gate_eligibility_version", "INTEGER", 1, None, 0),
        ("verification_runner_resolution_id", "TEXT", 1, None, 0),
        ("verification_runner_attempt_id", "TEXT", 0, None, 0),
        ("runner_implementation_digest", "TEXT", 1, None, 0),
        ("route", "TEXT", 1, None, 0),
        ("launch_state", "TEXT", 1, None, 0),
        ("outcome", "TEXT", 1, None, 0),
        ("reason", "TEXT", 0, None, 0),
        ("complete_plan", "INTEGER", 1, None, 0),
        ("total_step_count", "INTEGER", 1, None, 0),
        ("completed_step_count", "INTEGER", 1, None, 0),
        ("failed_step_ordinal", "INTEGER", 0, None, 0),
        ("started_at", "TEXT", 1, None, 0),
        ("finished_at", "TEXT", 1, None, 0),
        ("duration_ms", "INTEGER", 1, None, 0),
        ("cpu_time_ms", "INTEGER", 0, None, 0),
        ("peak_job_memory_bytes", "INTEGER", 0, None, 0),
        ("total_process_count", "INTEGER", 0, None, 0),
        ("sanitized_result_digest", "TEXT", 1, None, 0),
        ("created_at", "TEXT", 1, None, 0),
    ),
}

EXPECTED_INDEXES = {
    "idx_verification_runner_resolutions_parent": (
        "verification_runner_resolutions", 1, 0,
        ("project_id", "task_id", "target_generation", "verification_runner_resolution_id"),
    ),
    "idx_verification_runner_resolutions_task_generation": (
        "verification_runner_resolutions", 0, 0,
        ("project_id", "task_id", "target_generation"),
    ),
    "idx_verification_runner_attempts_parent": (
        "verification_runner_attempts", 1, 0,
        ("project_id", "task_id", "target_generation", "verification_runner_attempt_id"),
    ),
    "idx_verification_runner_attempts_task_generation": (
        "verification_runner_attempts", 0, 0,
        ("project_id", "task_id", "target_generation"),
    ),
    "idx_verification_runner_attempts_resolution": (
        "verification_runner_attempts", 0, 0,
        ("project_id", "task_id", "target_generation", "verification_runner_resolution_id"),
    ),
    "idx_verification_runner_sandbox_events_attempt_kind": (
        "verification_runner_sandbox_events", 0, 0,
        ("project_id", "task_id", "target_generation", "verification_runner_attempt_id", "event_kind"),
    ),
    "idx_verification_runner_observations_parent": (
        "verification_runner_observations", 1, 0,
        ("project_id", "task_id", "target_generation", "verification_runner_observation_id"),
    ),
    "idx_verification_runner_observations_task_generation": (
        "verification_runner_observations", 0, 0,
        ("project_id", "task_id", "target_generation"),
    ),
    "idx_verification_runner_observations_resolution": (
        "verification_runner_observations", 0, 0,
        ("project_id", "task_id", "target_generation", "verification_runner_resolution_id"),
    ),
    "idx_verification_runner_observations_attempt": (
        "verification_runner_observations", 0, 1,
        ("project_id", "task_id", "target_generation", "verification_runner_attempt_id"),
    ),
}

EXPECTED_FOREIGN_KEYS = {
    "verification_runner_resolutions": (
        ("artifact_manifests", (("project_id", "project_id"), ("task_id", "task_id"), ("artifact_manifest_id", "artifact_manifest_id")), "RESTRICT", "RESTRICT"),
        ("authority_snapshots", (("project_id", "project_id"), ("task_id", "task_id"), ("authority_snapshot_id", "authority_snapshot_id")), "RESTRICT", "RESTRICT"),
        ("contract_criteria", (("project_id", "project_id"), ("task_id", "task_id"), ("verification_criterion_id", "criterion_id")), "RESTRICT", "RESTRICT"),
        ("tasks", (("project_id", "project_id"), ("task_id", "task_id")), "RESTRICT", "RESTRICT"),
    ),
    "verification_runner_attempts": (
        ("verification_runner_resolutions", (("project_id", "project_id"), ("task_id", "task_id"), ("target_generation", "target_generation"), ("verification_runner_resolution_id", "verification_runner_resolution_id")), "RESTRICT", "RESTRICT"),
    ),
    "verification_runner_observations": (
        ("verification_runner_attempts", (("project_id", "project_id"), ("task_id", "task_id"), ("target_generation", "target_generation"), ("verification_runner_attempt_id", "verification_runner_attempt_id")), "RESTRICT", "RESTRICT"),
        ("verification_runner_resolutions", (("project_id", "project_id"), ("task_id", "task_id"), ("target_generation", "target_generation"), ("verification_runner_resolution_id", "verification_runner_resolution_id")), "RESTRICT", "RESTRICT"),
    ),
    "verification_runner_sandbox_events": (
        ("verification_runner_attempts", (("project_id", "project_id"), ("task_id", "task_id"), ("target_generation", "target_generation"), ("verification_runner_attempt_id", "verification_runner_attempt_id")), "RESTRICT", "RESTRICT"),
        ("verification_runner_observations", (("project_id", "project_id"), ("task_id", "task_id"), ("target_generation", "target_generation"), ("terminal_observation_id", "verification_runner_observation_id")), "RESTRICT", "RESTRICT"),
    ),
    "completion_evidence_bundles": (
        ("artifact_manifests", (("project_id", "project_id"), ("task_id", "task_id"), ("artifact_manifest_id", "artifact_manifest_id")), "NO ACTION", "NO ACTION"),
        ("authority_snapshots", (("project_id", "project_id"), ("task_id", "task_id"), ("authority_snapshot_id", "authority_snapshot_id")), "NO ACTION", "NO ACTION"),
        ("contract_criteria", (("project_id", "project_id"), ("task_id", "task_id"), ("acceptance_criterion_id", "criterion_id")), "NO ACTION", "NO ACTION"),
        ("contract_criteria", (("project_id", "project_id"), ("task_id", "task_id"), ("verification_criterion_id", "criterion_id")), "NO ACTION", "NO ACTION"),
        ("task_completion_cycles", (("completion_cycle_id", "completion_cycle_id"),), "NO ACTION", "NO ACTION"),
        ("tasks", (("project_id", "project_id"), ("task_id", "task_id")), "NO ACTION", "NO ACTION"),
        ("verification_receipts", (("verification_receipt_id", "verification_receipt_id"),), "NO ACTION", "NO ACTION"),
        ("verification_runner_observations", (("project_id", "project_id"), ("task_id", "task_id"), ("target_generation", "target_generation"), ("verification_runner_observation_id", "verification_runner_observation_id")), "RESTRICT", "RESTRICT"),
    ),
}

EXPECTED_SQL_SHA256 = {
    "completion_evidence_bundles": "3d6b76470563e115140be12050b3740f63db572e5becef56d08fdd0231fbd5e8",
    "verification_runner_resolutions": "2c7f341660b1c36131130d5ad0d21f8a73ab6094044e647aa8956f5104652795",
    "verification_runner_attempts": "aaea1459988d9ebe13ef7473533ca196223333cbf52f52328afe3a27b99df916",
    "verification_runner_sandbox_events": "41d9acdcb5ce81a38f0295af051bd21addb9ee74cca52786acf0a8a0b7af7ef9",
    "verification_runner_observations": "d14842ce02392531ffa22b7262bf563c7ef0591f7ff6f7bbd08f7c3af8c8a3d2",
    "idx_verification_runner_resolutions_parent": "c6ca93ffd3f9e81520378fa8049ba6bcc8a48bb2192459a4209e539b9155890a",
    "idx_verification_runner_resolutions_task_generation": "c2f8ba2d04acbd6253bfe3381c0f03f78b585dbd90095b708714627400dc66ae",
    "idx_verification_runner_attempts_parent": "953533490110f85eb0f06516beb067a61a1a5edfa66a702fbf9630a9b521b769",
    "idx_verification_runner_attempts_task_generation": "c2abd4601db403e40ac72acfa1acf861d214492a54ae10e1a81286c05f49d0d3",
    "idx_verification_runner_attempts_resolution": "f23318b7bbd8157c0c3bbb4ccca65d36450b2bd9c8806d47d1fc2f0002d5f46d",
    "idx_verification_runner_sandbox_events_attempt_kind": "b5a63df55f8a6ab2aefb484f293b0858d9ac5742e24f5616ea833378cdc732a3",
    "idx_verification_runner_observations_parent": "e06cfdd435257e76b599186bdf6d0b1965c9f40c035a1373c0f2980dd018df8c",
    "idx_verification_runner_observations_task_generation": "d6d145980e92d2d77b864899ceb2f37fba8db66cda8857184b5bec48fc78a9be",
    "idx_verification_runner_observations_resolution": "40ceb01c570657756a3a261e03458d4bb889ba51d87a5c31ae6b5879fa94c935",
    "idx_verification_runner_observations_attempt": "2087a8fd2c3a18eeca8449c5329f2c64af490c8d02abf03fd045e9b7df438520",
    "trg_criterion_evidence_links_matrix_insert": "2ef0e5d4d6e60ef77a80abc13cb61410f38d37c8149dca2ee50c514ee8f1e40c",
    "trg_verification_runner_resolutions_no_update": "9dea9bda4c5fe2166c7ef75ed11d8e41fa1413608775071991432b6f357d4e2d",
    "trg_verification_runner_resolutions_no_delete": "5e7989400ed591aff832c721d036b910ddbb21ed6b5f3bc2d930c7d04e26b7f9",
    "trg_verification_runner_attempts_no_update": "dc541ce5b558ae47e9efd55dce0e2bbe0ee33a6931ff43452f991e126b3050bf",
    "trg_verification_runner_attempts_no_delete": "d0f40cf27fa918bd878f339276b8821053116331cf8cae86406655705a0e1a99",
    "trg_verification_runner_sandbox_events_no_update": "356155ea92a0cb55b8c198b305dd165ab929b5d223ff55c481ed649e2cd77ef9",
    "trg_verification_runner_sandbox_events_no_delete": "e1c629e0d8c146968e549a27a0def6589e060e16ee2cd30979895e03ff0e64a7",
    "trg_verification_runner_observations_no_update": "4fac2ae4bbeaa52da17ff757ccd07f13d72f14a7262fb5364ec6c1a9ddb54a6c",
    "trg_verification_runner_observations_no_delete": "7649b5ba2b49de670d6bdc4d3d40fb1be9e018ebf110bf03681dd97c405ac7b3",
    "trg_verification_runner_resolutions_parent_insert": "7ac1e004b2827ac39acb8e6196d6a0e645cf763827ef98da394e1a697e08ad01",
    "trg_verification_runner_attempts_parent_insert": "09a64e5f0a7228b135ad64c87a2b4dc0c9e6418f087358b10cfffcc2d26052b5",
    "trg_verification_runner_sandbox_events_parent_insert": "391f21864f4306b672bd834dfc93539523eab38002cdef85c348c78132e4486b",
    "trg_verification_runner_observations_parent_insert": "855f23e45b0d19c64f148712dd1d1a0f9d051f0988ed97da9aa2462c68633423",
}


class _StaticRows:
    def __init__(self, rows: tuple[tuple[str, ...], ...]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str, ...]]:
        return list(self._rows)


class _BadIntegrityResultConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(
        self,
        statement: str,
        parameters: tuple[object, ...] = (),
    ) -> sqlite3.Cursor | _StaticRows:
        if " ".join(statement.upper().split()) == "PRAGMA INTEGRITY_CHECK":
            return _StaticRows((("injected integrity failure",),))
        return self._connection.execute(statement, parameters)


def labeled(character: str) -> str:
    return "sha256:" + character * 64


def artifact_manifest_digest(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(
        b"taskgov-artifact-manifest-v1\0" + canonical
    ).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def domain_digest(domain: bytes, value: object) -> str:
    return "sha256:" + hashlib.sha256(
        domain + canonical_json_bytes(value)
    ).hexdigest()


def evidence_reference_row(
    *,
    suffix: str,
    basis: dict[str, object],
    source_kind: str,
    source_state: str,
    source_id: str,
    source_projection: dict[str, object],
    completion_cycle_id: str | None = None,
) -> dict[str, object]:
    value = {
        "acceptance_criterion_id": basis["acceptance_criterion_id"],
        "assurance_class": "bound_attestation",
        "authority_snapshot_id": basis["authority_snapshot_id"],
        "completion_cycle_id": completion_cycle_id,
        "contract_revision": basis["contract_revision"],
        "producer_class": "trusted_caller",
        "producer_version": 1,
        "project_id": basis["project_id"],
        "source_id": source_id,
        "source_kind": source_kind,
        "source_projection": source_projection,
        "source_state": source_state,
        "target_base_revision": "",
        "target_generation": 1,
        "target_kind": "diff_fingerprint",
        "target_value": labeled("a"),
        "task_id": basis["task_id"],
        "verification_criterion_id": basis["verification_criterion_id"],
    }
    return {
        "evidence_reference_id": "tg_evidence_reference_" + suffix * 16,
        "project_id": basis["project_id"],
        "task_id": basis["task_id"],
        "source_kind": source_kind,
        "source_state": source_state,
        "source_id": source_id,
        "assurance_class": "bound_attestation",
        "producer_class": "trusted_caller",
        "producer_version": 1,
        "contract_revision": basis["contract_revision"],
        "authority_snapshot_id": basis["authority_snapshot_id"],
        "acceptance_criterion_id": basis["acceptance_criterion_id"],
        "verification_criterion_id": basis["verification_criterion_id"],
        "target_kind": "diff_fingerprint",
        "target_value": labeled("a"),
        "target_base_revision": "",
        "target_generation": 1,
        "completion_cycle_id": completion_cycle_id,
        "digest": domain_digest(
            b"taskgov-evidence-reference-v1\0",
            value,
        ),
        "created_at": NOW,
    }


def build_fixture_bundle_plan(
    *,
    basis: storage.NativeCompletionBundleBasis,
    cycle: storage.CompletionCycle,
    identity: storage.NativeCompletionIdentity,
) -> dict[str, object]:
    criterion_order = {"acceptance": 0, "verification": 1}
    relation_order = {
        "verification_attestation": 0,
        "review_assessment": 1,
        "review_finding": 2,
        "completion_basis": 3,
    }
    source_order = {
        "artifact_manifest": 0,
        "verification_receipt": 1,
        "review_receipt": 2,
        "review_finding": 3,
        "completion_evidence": 4,
    }
    criteria = tuple(dict(row) for row in basis.criteria)
    criterion_kinds = {
        str(row["criterion_id"]): str(row["criterion_kind"])
        for row in criteria
    }
    acceptance_id = next(
        identifier
        for identifier, kind in criterion_kinds.items()
        if kind == "acceptance"
    )
    verification_id = next(
        identifier
        for identifier, kind in criterion_kinds.items()
        if kind == "verification"
    )
    references = [
        dict(basis.artifact_reference),
        dict(basis.verification_reference or {}),
        *(dict(value) for value in basis.review_references),
        *(dict(value) for value in basis.finding_references if value is not None),
        dict(basis.completion_reference),
    ]
    reference_by_id = {
        str(row["evidence_reference_id"]): row for row in references
    }
    relations = [
        (
            acceptance_id,
            str(basis.artifact_reference["evidence_reference_id"]),
            "completion_basis",
        ),
        (
            acceptance_id,
            str(basis.completion_reference["evidence_reference_id"]),
            "completion_basis",
        ),
        *(
            (
                acceptance_id,
                str(reference["evidence_reference_id"]),
                "review_assessment",
            )
            for reference in basis.review_references
        ),
        *(
            (
                acceptance_id,
                str(reference["evidence_reference_id"]),
                "review_finding",
            )
            for finding, reference in zip(
                basis.findings,
                basis.finding_references,
                strict=True,
            )
            if reference is not None
            and finding["target_generation"] == cycle.review_target_generation
        ),
        (
            verification_id,
            str((basis.verification_reference or {})["evidence_reference_id"]),
            "verification_attestation",
        ),
    ]
    relations.sort(
        key=lambda value: (
            criterion_order[criterion_kinds[value[0]]],
            value[0],
            relation_order[value[2]],
            value[1],
        )
    )
    storage_links = []
    payload_links = []
    for ordinal, (criterion_id, reference_id, relation) in enumerate(relations):
        reference = reference_by_id[reference_id]
        payload_link = {
            "criterion_evidence_link_id": (
                "tg_criterion_evidence_link_" + format(ordinal + 1, "x") * 16
            ),
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
                "created_at": NOW,
            }
        )

    finding_snapshots = []
    for finding, reference in zip(
        basis.findings,
        basis.finding_references,
        strict=True,
    ):
        assert reference is not None
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
            "evidence_reference_id": reference["evidence_reference_id"],
            "assurance_class": reference["assurance_class"],
            "producer_class": reference["producer_class"],
            "producer_version": reference["producer_version"],
            "digest": None,
        }
        snapshot["digest"] = domain_digest(
            b"taskgov-completion-bundle-finding-snapshot-v1\0",
            {key: value for key, value in snapshot.items() if key != "digest"},
        )
        finding_snapshots.append(snapshot)

    projected_references = [
        {
            key: value
            for key, value in row.items()
            if key not in {"project_id", "task_id", "created_at"}
        }
        for row in references
    ]
    for row in projected_references:
        row["target_base_revision"] = row["target_base_revision"] or None
    projected_references.sort(
        key=lambda row: (
            source_order[str(row["source_kind"])],
            str(row["source_id"]),
            str(row["evidence_reference_id"]),
        )
    )
    finding_snapshots.sort(
        key=lambda row: (
            int(row["target_generation"]),
            str(row["created_at"]),
            str(row["review_finding_id"]),
        )
    )
    snapshot = basis.authority_snapshot
    manifest = basis.artifact_manifest
    payload = {
        "artifact_manifest": {
            "artifact_manifest_id": manifest["artifact_manifest_id"],
            "state": manifest["state"],
            "object_format": manifest["object_format"],
            "comparison_base": manifest["comparison_base"],
            "digest": manifest["digest"],
            "omission_code": manifest["omission_code"],
            "entries": [],
        },
        "authority_snapshot": {
            "authority_snapshot_id": snapshot["authority_snapshot_id"],
            "generation": snapshot["generation"],
            "digest": snapshot["basis_digest"],
        },
        "bundle_id": identity.completion_evidence_bundle_id,
        "bundle_version": 1,
        "completion_cycle_id": identity.completion_cycle_id,
        "cycle_ordinal": identity.saved_cycle_ordinal,
        "sealed_at": NOW,
        "completion_evidence": {
            "kind": cycle.completion_evidence_kind,
            "revision": cycle.completion_evidence_revision,
            "reason": cycle.completion_evidence_reason,
            "external_revision_approved": int(cycle.external_revision_approved),
            "completion_commit_required": int(cycle.completion_commit_required),
            "completion_commit_hash": cycle.completion_commit_hash,
        },
        "contract": {
            "revision": snapshot["contract_revision"],
            "specified": snapshot["contract_state"] == "contract_specified",
            "scope": snapshot["contract_scope"],
            "acceptance": snapshot["contract_acceptance"],
            "constraints": snapshot["contract_constraints"],
            "authority_ref": snapshot["contract_authority_ref"],
        },
        "criteria": sorted(
            (
                {
                    "criterion_id": row["criterion_id"],
                    "kind": row["criterion_kind"],
                    "text": row["criterion_text"],
                    "digest": row["digest"],
                }
                for row in criteria
            ),
            key=lambda row: (
                criterion_order[str(row["kind"])], str(row["criterion_id"])
            ),
        ),
        "criterion_links": payload_links,
        "evidence_references": projected_references,
        "finding_snapshots": finding_snapshots,
        "omissions": ["artifact_content_not_observed"],
        "project_id": cycle.project_id,
        "review_receipts": [
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
        ],
        "source_schema_version": 19,
        "target": {
            "kind": cycle.review_target_kind,
            "value": cycle.review_target_value,
            "base_revision": cycle.review_target_base_revision or None,
            "generation": cycle.review_target_generation,
            "capture_version": 1,
        },
        "task": dict(basis.task),
        "verification_receipt": dict(basis.verification_receipt or {}),
    }
    payload_bytes = canonical_json_bytes(payload)
    return {
        "storage_links": tuple(storage_links),
        "reference_ids": tuple(
            str(row["evidence_reference_id"])
            for row in projected_references
        ),
        "finding_snapshots": tuple(finding_snapshots),
        "omission_mask": 4,
        "payload_bytes": payload_bytes,
        "bundle_digest": domain_digest(
            b"taskgov-completion-evidence-bundle-v1\0",
            payload,
        ),
    }


def projection_payload_bytes(record: storage.ProjectionBundleRecord) -> bytes:
    bundle = record.bundle
    cycle = record.cycle
    manifest = record.artifact_manifest
    snapshot = record.authority_snapshot
    reference_keys = {
        "evidence_reference_id", "source_kind", "source_state", "source_id",
        "assurance_class", "producer_class", "producer_version",
        "contract_revision", "authority_snapshot_id",
        "acceptance_criterion_id", "verification_criterion_id", "target_kind",
        "target_value", "target_base_revision", "target_generation",
        "completion_cycle_id", "digest",
    }
    source_order = {
        "artifact_manifest": 0,
        "verification_receipt": 1,
        "review_receipt": 2,
        "review_finding": 3,
        "completion_evidence": 4,
    }
    references = [
        {key: row[key] for key in reference_keys}
        for row in record.evidence_references
    ]
    for row in references:
        row["target_base_revision"] = row["target_base_revision"] or None
    references.sort(
        key=lambda row: (
            source_order[str(row["source_kind"])],
            str(row["source_id"]),
            str(row["evidence_reference_id"]),
        )
    )
    links = [
        {
            "criterion_evidence_link_id": row.criterion_evidence_link_id,
            "criterion_id": row.criterion_id,
            "evidence_reference_id": row.evidence_reference_id,
            "relation": row.relation,
            "assurance_class": row.assurance_class,
            "producer_class": row.producer_class,
            "producer_version": row.producer_version,
        }
        for row in bundle.criterion_links
    ]
    findings = [
        {
            key: getattr(row, key)
            for key in (
                "review_finding_id", "review_receipt_id", "target_generation",
                "severity", "summary", "status", "resolution_summary",
                "created_at", "resolved_at", "evidence_reference_id",
                "assurance_class", "producer_class", "producer_version", "digest",
            )
        }
        for row in bundle.finding_snapshots
    ]
    omissions = [
        name
        for bit, name in enumerate(
            (
                "acceptance_criterion_absent",
                "verification_criterion_absent",
                "artifact_content_not_observed",
                "historical_finding_reference_absent",
            )
        )
        if bundle.omission_mask & (1 << bit)
    ]
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
                    "ordinal": row["ordinal"],
                    "kind": row["entry_kind"],
                    "old_path": row["old_path"],
                    "new_path": row["new_path"],
                    "before_mode": row["before_mode"],
                    "before_object_id": row["before_object_id"],
                    "after_mode": row["after_mode"],
                    "after_object_id": row["after_object_id"],
                }
                for row in record.artifact_entries
            ],
        },
        "authority_snapshot": {
            "authority_snapshot_id": snapshot["authority_snapshot_id"],
            "generation": snapshot["generation"],
            "digest": snapshot["basis_digest"],
        },
        "bundle_id": bundle.completion_evidence_bundle_id,
        "bundle_version": bundle.bundle_version,
        "completion_cycle_id": bundle.completion_cycle_id,
        "cycle_ordinal": bundle.cycle_ordinal,
        "sealed_at": bundle.sealed_at,
        "completion_evidence": {
            "kind": cycle.completion_evidence_kind,
            "revision": cycle.completion_evidence_revision,
            "reason": cycle.completion_evidence_reason,
            "external_revision_approved": int(cycle.external_revision_approved),
            "completion_commit_required": int(cycle.completion_commit_required),
            "completion_commit_hash": cycle.completion_commit_hash,
        },
        "contract": {
            "revision": snapshot["contract_revision"],
            "specified": snapshot["contract_state"] == "contract_specified",
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
            for row in record.criteria
        ],
        "criterion_links": links,
        "evidence_references": references,
        "finding_snapshots": findings,
        "omissions": omissions,
        "project_id": bundle.project_id,
        "review_receipts": [
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
            for value in record.review_receipts
        ],
        "source_schema_version": bundle.source_schema_version,
        "target": {
            "kind": bundle.target_kind,
            "value": bundle.target_value,
            "base_revision": bundle.target_base_revision or None,
            "generation": bundle.target_generation,
            "capture_version": bundle.target_capture_version,
        },
        "task": dict(record.task),
        "verification_receipt": record.verification_receipt,
    }
    return canonical_json_bytes(payload)


class R3ASchema20StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(
            prefix=".tmp-m242-r3a-storage-",
            dir=ROOT,
        )
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _fresh_v19(self, name: str = "case") -> Path:
        repo = self.root / name / "repo"
        db = self.root / name / "state" / "taskgov.sqlite"
        repo.mkdir(parents=True)
        target = storage.resolve_database_target(
            repo=repo,
            db=db,
            script_path=ROOT / "task-governance-tool" / "scripts" / "taskgov.py",
        )
        storage.initialize_database(target)
        with closing(storage.connect(db)) as connection:
            self.assertEqual(storage.current_schema_version(connection), 19)
        return db

    def _assert_v19_table_inventory(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        actual = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
        )
        self.assertEqual(actual, V19_TABLES)

    @staticmethod
    def _value_token(value: object) -> tuple[str, str]:
        if value is None:
            return ("null", "")
        if type(value) is int:
            return ("integer", str(value))
        if type(value) is float:
            return ("real", value.hex())
        if type(value) is str:
            return ("text", value)
        if type(value) is bytes:
            return ("blob", value.hex())
        raise AssertionError(f"unexpected SQLite value type: {type(value)!r}")

    @classmethod
    def _table_projection_snapshot(
        cls,
        connection: sqlite3.Connection,
        table_names: tuple[str, ...],
        *,
        column_basis: dict[str, tuple[str, ...]] | None = None,
    ) -> tuple[
        dict[str, tuple[str, ...]],
        tuple[
            tuple[
                str,
                tuple[str, ...],
                tuple[tuple[tuple[str, str], ...], ...],
            ],
            ...,
        ],
    ]:
        columns = {} if column_basis is None else dict(column_basis)
        snapshots = []
        for table_name in table_names:
            quoted_table = '"' + table_name.replace('"', '""') + '"'
            if table_name not in columns:
                columns[table_name] = tuple(
                    str(row["name"])
                    for row in connection.execute(
                        f"PRAGMA table_xinfo({quoted_table})"
                    ).fetchall()
                )
            selected = columns[table_name]
            quoted_columns = ",".join(
                '"' + name.replace('"', '""') + '"' for name in selected
            )
            rows = tuple(
                sorted(
                    tuple(cls._value_token(value) for value in row)
                    for row in connection.execute(
                        f"SELECT {quoted_columns} FROM {quoted_table}"
                    ).fetchall()
                )
            )
            snapshots.append((table_name, selected, rows))
        return columns, tuple(snapshots)

    def _logical_database_snapshot(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[
        tuple[tuple[str, str, str, str], ...],
        tuple[
            tuple[
                str,
                tuple[str, ...],
                tuple[tuple[tuple[str, str], ...], ...],
            ],
            ...,
        ],
    ]:
        table_names = tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
        )
        _columns, rows = self._table_projection_snapshot(
            connection,
            table_names,
        )
        return self._schema_projection(connection), rows

    def _assert_rehearsal_error_unchanged(
        self,
        db: Path,
        invoke: Callable[[], None],
    ) -> storage.StorageError:
        with closing(storage.connect(db)) as connection:
            before = self._logical_database_snapshot(connection)
        with self.assertRaises(storage.StorageError) as raised:
            invoke()
        with closing(storage.connect(db)) as connection:
            after = self._logical_database_snapshot(connection)
        self.assertEqual(after, before)
        return raised.exception

    @staticmethod
    def _schema_projection(
        connection: sqlite3.Connection,
    ) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            (
                str(row["type"]),
                str(row["name"]),
                str(row["tbl_name"]),
                ""
                if row["sql"] is None
                else " ".join(str(row["sql"]).strip().removesuffix(";").split()),
            )
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
        )

    @staticmethod
    def _append_table_columns(create_sql: str, *definitions: str) -> str:
        constraint = re.search(
            r",\s+(?=(?:UNIQUE|CHECK|FOREIGN\s+KEY)\s*\()",
            create_sql,
            flags=re.IGNORECASE,
        )
        if constraint is None or not definitions:
            raise AssertionError("invalid test-owned CREATE TABLE transformation")
        return (
            create_sql[: constraint.start()]
            + ", "
            + ", ".join(definitions)
            + create_sql[constraint.start() :]
        )

    @staticmethod
    def _normalized_sql_digest(sql: str) -> str:
        normalized = " ".join(sql.strip().removesuffix(";").split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _foreign_keys(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> tuple[tuple[object, ...], ...]:
        groups: dict[int, list[sqlite3.Row]] = {}
        for row in connection.execute(
            f'PRAGMA foreign_key_list("{table_name}")'
        ).fetchall():
            groups.setdefault(int(row["id"]), []).append(row)
        result = []
        for rows in groups.values():
            ordered = sorted(rows, key=lambda row: int(row["seq"]))
            result.append(
                (
                    str(ordered[0]["table"]),
                    tuple(
                        (str(row["from"]), str(row["to"]))
                        for row in ordered
                    ),
                    str(ordered[0]["on_update"]),
                    str(ordered[0]["on_delete"]),
                )
            )
        return tuple(sorted(result))

    def _assert_schema20_oracle(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        counts = {
            kind: int(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = ? AND name NOT LIKE 'sqlite_%'"
                    + (" AND sql IS NOT NULL" if kind == "index" else ""),
                    (kind,),
                ).fetchone()[0]
            )
            for kind in ("table", "index", "trigger")
        }
        self.assertEqual(counts, {"table": 35, "index": 42, "trigger": 59})

        for table_name, expected in RUNNER_TABLE_XINFO.items():
            actual = tuple(
                (
                    str(row["name"]),
                    str(row["type"]),
                    int(row["notnull"]),
                    row["dflt_value"],
                    int(row["pk"]),
                )
                for row in connection.execute(
                    f'PRAGMA table_xinfo("{table_name}")'
                ).fetchall()
            )
            self.assertEqual(actual, expected, table_name)
            self.assertEqual(
                self._foreign_keys(connection, table_name),
                EXPECTED_FOREIGN_KEYS[table_name],
                table_name,
            )

        task_columns = connection.execute(
            'PRAGMA table_xinfo("tasks")'
        ).fetchall()
        self.assertEqual(
            tuple(task_columns[-1]),
            (
                len(task_columns) - 1,
                "review_target_runner_basis_version",
                "INTEGER",
                1,
                "0",
                0,
                0,
            ),
        )
        tasks_table = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'tasks'"
        ).fetchone()
        self.assertIsNotNone(tasks_table)
        tasks_sql = " ".join(str(tasks_table["sql"]).strip().split())
        self.assertEqual(
            tasks_sql.count(
                "review_target_runner_basis_version INTEGER NOT NULL "
                "DEFAULT 0 CHECK (review_target_runner_basis_version "
                "IN (0, 2))"
            ),
            1,
        )
        cycle_columns = connection.execute(
            'PRAGMA table_xinfo("task_completion_cycles")'
        ).fetchall()
        self.assertEqual(
            tuple(
                (
                    str(row["name"]), str(row["type"]),
                    int(row["notnull"]), row["dflt_value"],
                    int(row["pk"]), int(row["hidden"]),
                )
                for row in cycle_columns[-2:]
            ),
            (
                ("verification_basis_kind", "TEXT", 0, None, 0, 0),
                ("verification_runner_observation_id", "TEXT", 0, None, 0, 0),
            ),
        )
        cycle_table = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'task_completion_cycles'"
        ).fetchone()
        self.assertIsNotNone(cycle_table)
        cycle_sql = " ".join(str(cycle_table["sql"]).strip().split())
        self.assertEqual(
            cycle_sql.count(
                "verification_basis_kind TEXT, "
                "verification_runner_observation_id TEXT"
            ),
            1,
        )
        self.assertEqual(cycle_sql.count("verification_basis_kind"), 1)
        self.assertEqual(
            cycle_sql.count("verification_runner_observation_id"),
            1,
        )
        self.assertIsNone(
            re.search(
                r"CHECK\s*\([^)]*(?:verification_basis_kind|"
                r"verification_runner_observation_id)",
                cycle_sql,
                flags=re.IGNORECASE,
            )
        )
        cycle_fk_columns = {
            str(row["from"])
            for row in connection.execute(
                'PRAGMA foreign_key_list("task_completion_cycles")'
            ).fetchall()
        }
        self.assertTrue(
            {
                "verification_basis_kind",
                "verification_runner_observation_id",
            }.isdisjoint(cycle_fk_columns)
        )
        bundle_xinfo = connection.execute(
            'PRAGMA table_xinfo("completion_evidence_bundles")'
        ).fetchall()
        bundle_columns = tuple(str(row["name"]) for row in bundle_xinfo)
        self.assertEqual(
            bundle_columns[17:21],
            (
                "verification_receipt_id",
                "verification_basis_kind",
                "verification_runner_observation_id",
                "omission_mask",
            ),
        )
        self.assertEqual(
            tuple(
                (
                    str(row["name"]), str(row["type"]), int(row["notnull"]),
                    row["dflt_value"], int(row["pk"]), int(row["hidden"]),
                )
                for row in bundle_xinfo[18:20]
            ),
            (
                ("verification_basis_kind", "TEXT", 0, None, 0, 0),
                ("verification_runner_observation_id", "TEXT", 0, None, 0, 0),
            ),
        )
        self.assertEqual(
            self._foreign_keys(connection, "completion_evidence_bundles"),
            EXPECTED_FOREIGN_KEYS["completion_evidence_bundles"],
        )

        for index_name, expected in EXPECTED_INDEXES.items():
            table_name, unique, partial, columns = expected
            row = connection.execute(
                "SELECT tbl_name, sql FROM sqlite_master "
                "WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()
            self.assertIsNotNone(row, index_name)
            index_row = next(
                value
                for value in connection.execute(
                    f'PRAGMA index_list("{table_name}")'
                ).fetchall()
                if str(value["name"]) == index_name
            )
            self.assertEqual(
                (
                    str(row["tbl_name"]),
                    int(index_row["unique"]),
                    int(index_row["partial"]),
                    tuple(
                        str(value["name"])
                        for value in connection.execute(
                            f'PRAGMA index_info("{index_name}")'
                        ).fetchall()
                    ),
                ),
                (table_name, unique, partial, columns),
                index_name,
            )

        actual_sql = {
            str(row["name"]): self._normalized_sql_digest(str(row["sql"]))
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE name IN ("
                + ",".join("?" for _ in EXPECTED_SQL_SHA256)
                + ")",
                tuple(EXPECTED_SQL_SHA256),
            ).fetchall()
        }
        self.assertEqual(actual_sql, EXPECTED_SQL_SHA256)
        expected_deferrability = {
            "verification_runner_resolutions": (4, 0),
            "verification_runner_attempts": (1, 0),
            "verification_runner_observations": (2, 0),
            "verification_runner_sandbox_events": (1, 1),
            "completion_evidence_bundles": (1, 1),
        }
        for name, (not_deferred, initially_deferred) in expected_deferrability.items():
            sql = str(
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = ?",
                    (name,),
                ).fetchone()[0]
            ).upper()
            self.assertEqual(sql.count("NOT DEFERRABLE"), not_deferred, name)
            self.assertEqual(
                sql.count("DEFERRABLE INITIALLY DEFERRED"),
                initially_deferred,
                name,
            )
        self.assertEqual(
            sum(name.endswith(("_no_update", "_no_delete")) for name in actual_sql),
            8,
        )
        for name in EXPECTED_SQL_SHA256:
            if name.endswith(("_no_update", "_no_delete")):
                sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_master WHERE name = ?",
                        (name,),
                    ).fetchone()[0]
                )
                self.assertRegex(
                    sql,
                    r"SELECT\s+RAISE\(ABORT,\s*'runner_storage_immutable'\)",
                )

    def test_private_migration_inventory_preservation_and_reentry(self) -> None:
        db = self._fresh_v19()
        with closing(storage.connect(db)) as connection:
            fixture, before_payload = self._seed_nonempty_v19_closure(connection)
            self._assert_v19_table_inventory(connection)
            independent_columns, independent_before = (
                self._table_projection_snapshot(
                    connection,
                    V19_BUSINESS_TABLES,
                )
            )
            before_schema = self._schema_projection(connection)
            before_schema_sql = {row[1]: row[3] for row in before_schema}
            before = storage._selected_table_projection_snapshot(
                connection,
                V19_BUSINESS_TABLES,
            )
            original_columns = {name: value[0] for name, value in before.items()}
            nonempty = {
                table_name: int(
                    connection.execute(
                        f'SELECT COUNT(*) FROM "{table_name}"'
                    ).fetchone()[0]
                )
                for table_name in (
                    "tasks",
                    "task_contract_revisions",
                    "authority_snapshots",
                    "contract_criteria",
                    "authority_snapshot_criteria",
                    "artifact_manifests",
                    "verification_receipts",
                    "review_receipts",
                    "review_findings",
                    "task_completion_cycles",
                    "completion_evidence_bundles",
                    "completion_bundle_members",
                    "completion_bundle_finding_snapshots",
                    "evidence_references",
                    "criterion_evidence_links",
                )
            }
            self.assertTrue(all(value > 0 for value in nonempty.values()), nonempty)
            self.assertEqual(nonempty["evidence_references"], 5)
            self.assertEqual(nonempty["criterion_evidence_links"], 5)
            self.assertEqual(nonempty["completion_bundle_members"], 10)
            id_columns = {
                "tasks": "task_id",
                "task_contract_revisions": "contract_revision_id",
                "authority_snapshots": "authority_snapshot_id",
                "contract_criteria": "criterion_id",
                "artifact_manifests": "artifact_manifest_id",
                "verification_receipts": "verification_receipt_id",
                "review_receipts": "review_receipt_id",
                "review_findings": "review_finding_id",
                "task_completion_cycles": "completion_cycle_id",
                "completion_evidence_bundles": "completion_evidence_bundle_id",
            }
            before_ids = {
                table_name: tuple(
                    str(row[0])
                    for row in connection.execute(
                        f'SELECT "{column_name}" FROM "{table_name}" '
                        f'ORDER BY "{column_name}"'
                    ).fetchall()
                )
                for table_name, column_name in id_columns.items()
            }
            bundle_before = dict(
                connection.execute(
                    "SELECT * FROM completion_evidence_bundles"
                ).fetchone()
            )
            self.assertEqual(bundle_before["bundle_version"], 1)
            self.assertEqual(bundle_before["source_schema_version"], 19)
            self.assertEqual(bundle_before["payload_size_bytes"], len(before_payload))
            self.assertEqual(
                bundle_before["bundle_digest"],
                domain_digest(
                    b"taskgov-completion-evidence-bundle-v1\0",
                    json.loads(before_payload.decode("utf-8")),
                ),
            )

        storage.rehearse_schema20_storage(db)
        with closing(storage.connect(db)) as connection:
            storage.validate_schema20_storage(connection)
            self.assertEqual(storage.current_schema_version(connection), 20)
            self._assert_schema20_oracle(connection)
            _columns, independent_after = self._table_projection_snapshot(
                connection,
                V19_BUSINESS_TABLES,
                column_basis=independent_columns,
            )
            self.assertEqual(independent_after, independent_before)
            after = storage._selected_table_projection_snapshot(
                connection,
                V19_BUSINESS_TABLES,
                column_basis=original_columns,
            )
            self.assertEqual(after, before)
            changed_or_new = {
                "tasks",
                "task_completion_cycles",
                *EXPECTED_SQL_SHA256,
            }
            after_schema = self._schema_projection(connection)
            after_schema_sql = {row[1]: row[3] for row in after_schema}
            self.assertEqual(
                after_schema_sql["tasks"],
                self._append_table_columns(
                    before_schema_sql["tasks"],
                    TASK_R3A_COLUMN_SQL,
                ),
            )
            self.assertEqual(
                after_schema_sql["task_completion_cycles"],
                self._append_table_columns(
                    before_schema_sql["task_completion_cycles"],
                    *CYCLE_R3A_COLUMN_SQL,
                ),
            )
            self.assertEqual(
                tuple(row for row in after_schema if row[1] not in changed_or_new),
                tuple(row for row in before_schema if row[1] not in changed_or_new),
            )
            self.assertEqual(
                tuple(
                    tuple(row)
                    for row in connection.execute(
                        "SELECT version, name FROM schema_migrations "
                        "WHERE version >= 20 ORDER BY version"
                    ).fetchall()
                ),
                ((20, "verification_runner_shadow"),),
            )
            self.assertEqual(
                {
                    table_name: tuple(
                        str(row[0])
                        for row in connection.execute(
                            f'SELECT "{id_columns[table_name]}" '
                            f'FROM "{table_name}" '
                            f'ORDER BY "{id_columns[table_name]}"'
                        ).fetchall()
                    )
                    for table_name in before_ids
                },
                before_ids,
            )
            projection = storage._capture_evidence_projection_basis_rows(
                connection,
                project_id=str(fixture["project_id"]),
            )
            self.assertEqual(len(projection.native_bundles), 1)
            after_payload = projection_payload_bytes(projection.native_bundles[0])
            self.assertEqual(after_payload, before_payload)
            after_bundle = dict(
                connection.execute(
                    "SELECT * FROM completion_evidence_bundles"
                ).fetchone()
            )
            self.assertEqual(after_bundle["bundle_digest"], bundle_before["bundle_digest"])
            self.assertEqual(
                after_bundle["payload_size_bytes"],
                bundle_before["payload_size_bytes"],
            )
            self.assertIsNone(after_bundle["verification_basis_kind"])
            self.assertIsNone(after_bundle["verification_runner_observation_id"])
            self.assertEqual(
                sum(
                    int(
                        connection.execute(
                            f'SELECT COUNT(*) FROM "{table_name}"'
                        ).fetchone()[0]
                    )
                    for table_name in storage._R3A_SCHEMA20_RUNNER_TABLES
                ),
                0,
            )
            self.assertEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM tasks "
                        "WHERE review_target_runner_basis_version != 0"
                    ).fetchone()[0]
                ),
                0,
            )
            self.assertEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM evidence_references "
                        "WHERE source_kind = 'runner_observation'"
                    ).fetchone()[0]
                ),
                0,
            )
            self.assertEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM criterion_evidence_links "
                        "WHERE relation = 'runner_observation'"
                    ).fetchone()[0]
                ),
                0,
            )
            self.assertEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM task_completion_cycles "
                        "WHERE verification_basis_kind IS NOT NULL "
                        "OR verification_runner_observation_id IS NOT NULL"
                    ).fetchone()[0]
                ),
                0,
            )
            self.assertEqual(
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM completion_evidence_bundles "
                        "WHERE verification_basis_kind IS NOT NULL "
                        "OR verification_runner_observation_id IS NOT NULL"
                    ).fetchone()[0]
                ),
                0,
            )
        before_reentry = hashlib.sha256(db.read_bytes()).hexdigest()
        storage.rehearse_schema20_storage(db)
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before_reentry)

    def test_rollback_contention_and_schema_state_fail_closed(self) -> None:
        for fail_stage in FAIL_STAGES:
            with self.subTest(fail_stage=fail_stage):
                rollback_db = self._fresh_v19("rollback-" + fail_stage)
                with closing(storage.connect(rollback_db)) as connection:
                    self._seed_nonempty_v19_closure(connection)
                    self.assertEqual(
                        int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                        1,
                    )
                error = self._assert_rehearsal_error_unchanged(
                    rollback_db,
                    lambda: storage.rehearse_schema20_storage(
                        rollback_db,
                        fail_stage=fail_stage,
                    ),
                )
                self.assertEqual(error.code, "internal_error")
                self.assertEqual(
                    error.message,
                    "injected private schema-v20 rehearsal failure",
                )
                with closing(storage.connect(rollback_db)) as connection:
                    self.assertEqual(storage.current_schema_version(connection), 19)
                    self.assertIsNone(
                        connection.execute(
                            "SELECT 1 FROM schema_migrations WHERE version = 20"
                        ).fetchone()
                    )
                    self.assertEqual(
                        int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                        1,
                    )

        contention_db = self._fresh_v19("contention")
        with closing(storage.connect(contention_db)) as connection:
            self._seed_nonempty_v19_closure(connection)
            contention_before = self._logical_database_snapshot(connection)
        original_connect = sqlite3.connect
        with closing(storage.connect(contention_db)) as owner:
            owner.execute("BEGIN IMMEDIATE")

            def no_wait_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
                return original_connect(*args, **{**kwargs, "timeout": 0.0})

            with mock.patch.object(storage.sqlite3, "connect", side_effect=no_wait_connect):
                with self.assertRaises(storage.StorageError) as raised:
                    storage.rehearse_schema20_storage(contention_db)
            self.assertEqual(raised.exception.code, "database_busy")
            owner.rollback()
        with closing(storage.connect(contention_db)) as connection:
            self.assertEqual(storage.current_schema_version(connection), 19)
            self.assertEqual(
                self._logical_database_snapshot(connection),
                contention_before,
            )
            self.assertEqual(
                int(connection.execute("PRAGMA foreign_keys").fetchone()[0]),
                1,
            )

        marker_db = self._fresh_v19("marker")
        with closing(storage.connect(marker_db)) as connection:
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (20, 'verification_runner_shadow', ?)",
                (NOW,),
            )
            connection.commit()
        marker_error = self._assert_rehearsal_error_unchanged(
            marker_db,
            lambda: storage.rehearse_schema20_storage(marker_db),
        )
        self.assertEqual(marker_error.code, "project_state_unreadable")

        partial_db = self._fresh_v19("partial")
        with closing(storage.connect(partial_db)) as connection:
            connection.execute("CREATE TABLE verification_runner_resolutions(x TEXT)")
            connection.commit()
        partial_error = self._assert_rehearsal_error_unchanged(
            partial_db,
            lambda: storage.rehearse_schema20_storage(partial_db),
        )
        self.assertEqual(partial_error.code, "migration_required")

        changed_db = self._fresh_v19("changed")
        storage.rehearse_schema20_storage(changed_db)
        with closing(storage.connect(changed_db)) as connection:
            connection.execute(
                "DROP INDEX idx_verification_runner_attempts_resolution"
            )
            connection.execute(
                "CREATE INDEX idx_verification_runner_attempts_resolution "
                "ON verification_runner_attempts(project_id, task_id)"
            )
            connection.commit()
        changed_error = self._assert_rehearsal_error_unchanged(
            changed_db,
            lambda: storage.rehearse_schema20_storage(changed_db),
        )
        self.assertEqual(changed_error.code, "project_state_unreadable")

        drift_db = self._fresh_v19("drift")
        storage.rehearse_schema20_storage(drift_db)
        with closing(storage.connect(drift_db)) as connection:
            connection.execute("DROP INDEX idx_verification_runner_attempts_resolution")
            connection.commit()
        drift_error = self._assert_rehearsal_error_unchanged(
            drift_db,
            lambda: storage.rehearse_schema20_storage(drift_db),
        )
        self.assertEqual(drift_error.code, "project_state_unreadable")

        later_db = self._fresh_v19("later")
        storage.rehearse_schema20_storage(later_db)
        with closing(storage.connect(later_db)) as connection:
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (21, 'future_schema', ?)",
                (NOW,),
            )
            connection.commit()
        later_error = self._assert_rehearsal_error_unchanged(
            later_db,
            lambda: storage.rehearse_schema20_storage(later_db),
        )
        self.assertEqual(later_error.code, "migration_required")

        extra_db = self._fresh_v19("extra")
        with closing(storage.connect(extra_db)) as connection:
            connection.execute(
                "CREATE TABLE unrelated_extension("
                "key TEXT PRIMARY KEY, integer_value INTEGER, "
                "real_value REAL, text_value TEXT, blob_value BLOB, "
                "nullable_value TEXT)"
            )
            connection.execute(
                "INSERT INTO unrelated_extension VALUES (?, ?, ?, ?, ?, ?)",
                ("row", 7, 1.25, "preserve", b"\x00\xff", None),
            )
            connection.commit()
            extra_schema_before = tuple(
                row
                for row in self._schema_projection(connection)
                if row[1] == "unrelated_extension"
                or row[2] == "unrelated_extension"
            )
            extra_columns, extra_rows_before = self._table_projection_snapshot(
                connection,
                ("unrelated_extension",),
            )
        storage.rehearse_schema20_storage(extra_db)
        with closing(storage.connect(extra_db)) as connection:
            storage.validate_schema20_storage(connection)
            self.assertTrue(storage.table_exists(connection, "unrelated_extension"))
            self.assertEqual(
                tuple(
                    row
                    for row in self._schema_projection(connection)
                    if row[1] == "unrelated_extension"
                    or row[2] == "unrelated_extension"
                ),
                extra_schema_before,
            )
            _columns, extra_rows_after = self._table_projection_snapshot(
                connection,
                ("unrelated_extension",),
                column_basis=extra_columns,
            )
            self.assertEqual(extra_rows_after, extra_rows_before)

    def test_integrity_and_foreign_key_failures_roll_back(self) -> None:
        for mode in ("integrity", "foreign_key"):
            with self.subTest(mode=mode):
                db = self._fresh_v19(mode)
                with closing(storage.connect(db)) as connection:
                    self._seed_nonempty_v19_closure(connection)
                original_check = storage._schema20_integrity_checks
                check_count = 0

                def injected_check(connection: sqlite3.Connection) -> None:
                    nonlocal check_count
                    check_count += 1
                    if check_count == 2 and mode == "integrity":
                        original_check(_BadIntegrityResultConnection(connection))
                        return
                    if check_count == 2 and mode == "foreign_key":
                        connection.execute(
                            "INSERT INTO managed_backup_generations("
                            "generation_id, project_id, published_at, "
                            "publication_retention) VALUES (?, ?, ?, 1)",
                            (
                                "tg_backup_" + "f" * 32,
                                "tg_project_" + "f" * 32,
                                NOW,
                            ),
                        )
                    original_check(connection)

                with mock.patch.object(
                    storage,
                    "_schema20_integrity_checks",
                    side_effect=injected_check,
                ):
                    error = self._assert_rehearsal_error_unchanged(
                        db,
                        lambda: storage.rehearse_schema20_storage(db),
                    )
                self.assertEqual(check_count, 2)
                self.assertEqual(error.code, "project_state_unreadable")
                with closing(storage.connect(db)) as connection:
                    self.assertEqual(storage.current_schema_version(connection), 19)
                    self.assertIsNone(
                        connection.execute(
                            "SELECT 1 FROM schema_migrations WHERE version = 20"
                        ).fetchone()
                    )

    @staticmethod
    def _insert_bundle(
        connection: sqlite3.Connection,
        values: dict[str, object],
    ) -> None:
        columns = (
            "completion_evidence_bundle_id", "project_id", "task_id",
            "completion_cycle_id", "cycle_ordinal", "source_schema_version",
            "bundle_version", "contract_revision", "authority_snapshot_id",
            "acceptance_criterion_id", "verification_criterion_id",
            "target_kind", "target_value", "target_base_revision",
            "target_generation", "target_capture_version",
            "artifact_manifest_id", "verification_receipt_id",
            "verification_basis_kind", "verification_runner_observation_id",
            "omission_mask", "sealed_at", "bundle_digest",
            "payload_size_bytes",
        )
        connection.execute(
            "INSERT INTO completion_evidence_bundles("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(values[name] for name in columns),
        )

    def test_bundle_tagged_union_keeps_runner_pointer_null(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute(storage._completion_evidence_bundle_v20_table_sql())
            base: dict[str, object] = {
                "completion_evidence_bundle_id": (
                    "tg_completion_evidence_bundle_" + "1" * 16
                ),
                "project_id": "project",
                "task_id": "task",
                "completion_cycle_id": "tg_completion_cycle_" + "2" * 16,
                "cycle_ordinal": 1,
                "source_schema_version": 19,
                "bundle_version": 1,
                "contract_revision": 0,
                "authority_snapshot_id": "tg_authority_snapshot_" + "3" * 16,
                "acceptance_criterion_id": None,
                "verification_criterion_id": None,
                "target_kind": "diff_fingerprint",
                "target_value": labeled("4"),
                "target_base_revision": "",
                "target_generation": 1,
                "target_capture_version": 1,
                "artifact_manifest_id": "tg_artifact_manifest_" + "5" * 16,
                "verification_receipt_id": None,
                "verification_basis_kind": None,
                "verification_runner_observation_id": None,
                "omission_mask": 0,
                "sealed_at": NOW,
                "bundle_digest": labeled("6"),
                "payload_size_bytes": 1,
            }
            self._insert_bundle(connection, base)
            connection.execute("DELETE FROM completion_evidence_bundles")
            for ordinal, delta in enumerate(
                (
                    {
                        "verification_basis_kind": "caller_attestation",
                        "verification_receipt_id": (
                            "tg_verification_receipt_" + "7" * 16
                        ),
                    },
                    {"verification_basis_kind": "not_required"},
                ),
                start=2,
            ):
                self._insert_bundle(
                    connection,
                    {
                        **base,
                        "completion_evidence_bundle_id": (
                            "tg_completion_evidence_bundle_"
                            + format(ordinal, "x") * 16
                        ),
                        "source_schema_version": 20,
                        "bundle_version": 2,
                        **delta,
                    },
                )
                connection.execute("DELETE FROM completion_evidence_bundles")
            invalid = {
                **base,
                "completion_evidence_bundle_id": (
                    "tg_completion_evidence_bundle_" + "f" * 16
                ),
                "source_schema_version": 20,
                "bundle_version": 2,
                "verification_basis_kind": "not_required",
                "verification_runner_observation_id": (
                    "tg_verification_runner_observation_" + "8" * 16
                ),
            }
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_bundle(connection, invalid)
            with self.assertRaises(sqlite3.IntegrityError):
                self._insert_bundle(
                    connection,
                    {
                        **base,
                        "completion_evidence_bundle_id": (
                            "tg_completion_evidence_bundle_" + "e" * 16
                        ),
                        "source_schema_version": 19,
                        "bundle_version": 2,
                    },
                )

    def _seed_target(
        self,
        connection: sqlite3.Connection,
        *,
        suffix: str,
        acceptance: str | None,
        invalid_acceptance: str | None = None,
        review_tier: int = 2,
    ) -> dict[str, object]:
        project_id = str(
            connection.execute("SELECT project_id FROM project_meta").fetchone()[0]
        )
        task_id = "tg_task_" + suffix * 16
        revision = 1
        acceptance_text = acceptance or ""
        connection.execute(
            """
            INSERT INTO tasks(
              task_id, project_id, title, description, kind, lane, lane_order,
              priority, status, blocked_reason, pause_reason, review_tier,
              verification, tags, created_at, updated_at,
              current_contract_revision
            ) VALUES (?, ?, 'Runner storage fixture', '', 'optional', '', NULL,
                      'normal', 'in_progress', '', '', ?,
                      'python -m unittest', '', ?, ?, ?)
            """,
            (task_id, project_id, review_tier, NOW, NOW, revision),
        )
        connection.execute(
            """
            INSERT INTO task_contract_revisions(
              contract_revision_id, task_id, project_id, revision,
              scope, acceptance, constraints_text, authority_ref,
              change_reason, created_at
            ) VALUES (?, ?, ?, 1, 'storage', ?, '', 'docs/design.md', '', ?)
            """,
            (
                "tg_contract_" + suffix * 16,
                task_id,
                project_id,
                acceptance_text,
                NOW,
            ),
        )
        if acceptance is None:
            snapshot_id = "tg_authority_snapshot_" + suffix * 16
            verification_id = "tg_contract_criterion_" + suffix * 16
            verification_digest = storage.contract_criterion_digest(
                "verification",
                "python -m unittest",
            )
            connection.execute(
                """
                INSERT INTO contract_criteria(
                  criterion_id, project_id, task_id, criterion_kind,
                  criterion_text, digest, created_at
                ) VALUES (?, ?, ?, 'verification', 'python -m unittest', ?, ?)
                """,
                (verification_id, project_id, task_id, verification_digest, NOW),
            )
            connection.execute(
                """
                INSERT INTO authority_snapshots(
                  authority_snapshot_id, project_id, task_id, generation,
                  task_title, task_description, review_tier, verification,
                  verification_digest, contract_revision, contract_state,
                  contract_scope, contract_acceptance, contract_constraints,
                  contract_authority_ref, basis_digest, producer_class,
                  producer_version, created_at
                ) VALUES (?, ?, ?, 1, 'Runner storage fixture', '', 2,
                          'python -m unittest', ?, 1, 'contract_specified',
                          'storage', '', '', 'docs/design.md', ?,
                          'taskgov_core', 1, ?)
                """,
                (
                    snapshot_id,
                    project_id,
                    task_id,
                    storage._verification_expectation_digest(
                        "python -m unittest"
                    ),
                    labeled("0"),
                    NOW,
                ),
            )
            connection.execute(
                """
                INSERT INTO authority_snapshot_criteria(
                  project_id, task_id, authority_snapshot_id,
                  criterion_kind, criterion_id
                ) VALUES (?, ?, ?, 'verification', ?)
                """,
                (project_id, task_id, snapshot_id, verification_id),
            )
            connection.execute(
                """
                UPDATE tasks
                   SET current_authority_snapshot_id = ?,
                       current_authority_snapshot_generation = 1
                 WHERE project_id = ? AND task_id = ?
                """,
                (snapshot_id, project_id, task_id),
            )
            acceptance_id = None
        else:
            binding = storage.capture_or_reuse_current_authority_snapshot_locked(
                connection,
                project_id=project_id,
                task_id=task_id,
                created_at=NOW,
            )
            snapshot_id = binding.authority_snapshot_id
            verification_id = binding.verification_criterion_id
            acceptance_id = binding.acceptance_criterion_id
        if invalid_acceptance == "wrong_kind":
            acceptance_id = verification_id
        elif invalid_acceptance == "missing_membership":
            acceptance_id = "tg_contract_criterion_" + suffix * 15 + "f"
            connection.execute(
                """
                INSERT INTO contract_criteria(
                  criterion_id, project_id, task_id, criterion_kind,
                  criterion_text, digest, created_at
                ) VALUES (?, ?, ?, 'acceptance', 'unlinked acceptance', ?, ?)
                """,
                (acceptance_id, project_id, task_id, labeled("9"), NOW),
            )
        manifest_id = "tg_artifact_manifest_" + suffix * 16
        manifest_value = {
            "acceptance_criterion_id": acceptance_id,
            "authority_snapshot_id": snapshot_id,
            "comparison_base": None,
            "entries": [],
            "object_format": None,
            "omission_code": "artifact_content_not_observed",
            "state": "opaque_target",
            "target_base_revision": "",
            "target_generation": 1,
            "target_kind": "diff_fingerprint",
            "target_value": labeled("a"),
            "verification_criterion_id": verification_id,
        }
        connection.execute(
            """
            INSERT INTO artifact_manifests(
              artifact_manifest_id, project_id, task_id, state, object_format,
              comparison_base, target_kind, target_value,
              target_base_revision, target_generation, authority_snapshot_id,
              acceptance_criterion_id, verification_criterion_id,
              omission_code, entry_count, digest, created_at
            ) VALUES (?, ?, ?, 'opaque_target', NULL, NULL,
                      'diff_fingerprint', ?, '', 1, ?, ?, ?,
                      'artifact_content_not_observed', 0, ?, ?)
            """,
            (
                manifest_id,
                project_id,
                task_id,
                labeled("a"),
                snapshot_id,
                acceptance_id,
                verification_id,
                artifact_manifest_digest(manifest_value),
                NOW,
            ),
        )
        connection.execute(
            """
            UPDATE tasks
               SET review_target_kind = 'diff_fingerprint',
                   review_target_value = ?,
                   review_target_base_revision = '',
                   review_target_generation = 1,
                   review_target_capture_version = 1,
                   review_target_authority_snapshot_id = ?,
                   review_target_acceptance_criterion_id = ?,
                   review_target_verification_criterion_id = ?,
                   review_target_artifact_manifest_id = ?
             WHERE project_id = ? AND task_id = ?
            """,
            (
                labeled("a"),
                snapshot_id,
                acceptance_id,
                verification_id,
                manifest_id,
                project_id,
                task_id,
            ),
        )
        snapshot = connection.execute(
            "SELECT verification_digest FROM authority_snapshots "
            "WHERE authority_snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        criterion = connection.execute(
            "SELECT digest FROM contract_criteria WHERE criterion_id = ?",
            (verification_id,),
        ).fetchone()
        return {
            "project_id": project_id,
            "task_id": task_id,
            "contract_revision": revision,
            "authority_snapshot_id": snapshot_id,
            "acceptance_criterion_id": acceptance_id,
            "verification_criterion_id": verification_id,
            "verification_expectation_digest": snapshot[0],
            "verification_criterion_digest": criterion[0],
            "artifact_manifest_id": manifest_id,
        }

    def _seed_nonempty_v19_closure(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[dict[str, object], bytes]:
        connection.execute("BEGIN IMMEDIATE")
        basis = self._seed_target(
            connection,
            suffix="a",
            acceptance="storage migration preserves this closure",
            review_tier=0,
        )
        manifest = dict(
            connection.execute(
                "SELECT * FROM artifact_manifests WHERE artifact_manifest_id = ?",
                (basis["artifact_manifest_id"],),
            ).fetchone()
        )
        artifact_reference = evidence_reference_row(
            suffix="1",
            basis=basis,
            source_kind="artifact_manifest",
            source_state="opaque_target",
            source_id=str(basis["artifact_manifest_id"]),
            source_projection={
                "artifact_manifest_id": basis["artifact_manifest_id"],
                "state": "opaque_target",
                "target_kind": "diff_fingerprint",
                "digest": manifest["digest"],
                "omission_code": "artifact_content_not_observed",
            },
        )
        storage.persist_evidence_reference_locked(
            connection,
            reference=artifact_reference,
        )

        with mock.patch.object(storage, "utc_now", return_value=NOW):
            verification_receipt = storage.insert_verification_receipt_locked(
                connection,
                project_id=str(basis["project_id"]),
                task_id=str(basis["task_id"]),
                contract_revision=1,
                verification_expectation_digest=storage.verification_expectation_digest(
                    "python -m unittest"
                ),
                command_label="taskgov-owned-verification-subject-v1",
                result="pass",
                duration_ms=1,
                scope_coverage="full",
                target_kind="diff_fingerprint",
                target_value=labeled("a"),
                target_base_revision="",
                target_generation=1,
                verification_subject_basis_version=1,
                subject_authority_snapshot_id=str(basis["authority_snapshot_id"]),
                subject_verification_criterion_id=str(
                    basis["verification_criterion_id"]
                ),
            )
        verification_reference = evidence_reference_row(
            suffix="2",
            basis=basis,
            source_kind="verification_receipt",
            source_state="recorded",
            source_id=str(verification_receipt["verification_receipt_id"]),
            source_projection={
                "verification_receipt_id": verification_receipt[
                    "verification_receipt_id"
                ],
                "subject_basis_version": 1,
                "authority_snapshot_id": basis["authority_snapshot_id"],
                "verification_criterion_id": basis["verification_criterion_id"],
                "result": verification_receipt["result"],
                "duration_ms": verification_receipt["duration_ms"],
                "scope_coverage": verification_receipt["scope_coverage"],
                "created_at": verification_receipt["created_at"],
            },
        )
        storage.persist_evidence_reference_locked(
            connection,
            reference=verification_reference,
        )

        review_receipt_id = "tg_review_receipt_" + "b" * 16
        stored_review = storage.insert_review_receipt_with_provenance_locked(
            connection,
            {
                "review_receipt_id": review_receipt_id,
                "task_id": basis["task_id"],
                "project_id": basis["project_id"],
                "reviewer_key": "storage-fixture",
                "receipt_kind": "not_required",
                "verdict": "not_required",
                "target_kind": "diff_fingerprint",
                "target_value": labeled("a"),
                "target_base_revision": "",
                "target_generation": 1,
                "summary": "Tier zero review is not required.",
                "user_approved": 0,
                "created_at": NOW,
            },
            None,
            (),
        )
        review_reference = evidence_reference_row(
            suffix="3",
            basis=basis,
            source_kind="review_receipt",
            source_state="recorded",
            source_id=review_receipt_id,
            source_projection={
                "review_receipt_id": review_receipt_id,
                "reviewer_key": "storage-fixture",
                "receipt_kind": "not_required",
                "verdict": "not_required",
                "summary": "Tier zero review is not required.",
                "user_approved": 0,
                "created_at": NOW,
                "review_provenance": None,
            },
        )
        storage.persist_evidence_reference_locked(
            connection,
            reference=review_reference,
        )

        finding_id = "tg_review_finding_" + "c" * 16
        connection.execute(
            """
            INSERT INTO review_findings(
              review_finding_id, review_receipt_id, severity, status, summary,
              resolution_summary, created_at, resolved_at
            ) VALUES (?, ?, 'low', 'open', ?, '', ?, NULL)
            """,
            (finding_id, review_receipt_id, "Preserved low finding.", NOW),
        )
        finding_reference = evidence_reference_row(
            suffix="4",
            basis=basis,
            source_kind="review_finding",
            source_state="recorded",
            source_id=finding_id,
            source_projection={
                "review_finding_id": finding_id,
                "review_receipt_id": review_receipt_id,
                "severity": "low",
                "summary": "Preserved low finding.",
                "created_at": NOW,
            },
        )
        storage.persist_evidence_reference_locked(
            connection,
            reference=finding_reference,
        )

        locked = dict(
            connection.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND task_id = ?",
                (basis["project_id"], basis["task_id"]),
            ).fetchone()
        )
        proposed = {
            **locked,
            "status": "done",
            "completed_at": NOW,
            "updated_at": NOW,
            "completion_evidence_kind": "commit_not_required",
            "completion_evidence_revision": "",
            "completion_evidence_reason": "",
            "external_revision_approved": 0,
            "completion_commit_required": 0,
            "completion_commit_hash": "",
        }
        identity = storage.allocate_native_completion_identity_locked(
            connection,
            project_id=str(basis["project_id"]),
            task_id=str(basis["task_id"]),
        )
        cycle = storage.prepare_native_completion_cycle_locked(
            connection,
            project_id=str(basis["project_id"]),
            task_id=str(basis["task_id"]),
            task_projection=proposed,
            recorded_at=NOW,
            verification_expectation_digest=storage.verification_expectation_digest(
                "python -m unittest"
            ),
            verification_receipt_id=str(
                verification_receipt["verification_receipt_id"]
            ),
            verification_subject_basis_version=1,
            subject_authority_snapshot_id=str(basis["authority_snapshot_id"]),
            subject_verification_criterion_id=str(
                basis["verification_criterion_id"]
            ),
            completion_identity=identity,
        )
        completion_reference = evidence_reference_row(
            suffix="5",
            basis=basis,
            source_kind="completion_evidence",
            source_state="commit_not_required",
            source_id=cycle.completion_cycle_id,
            source_projection={
                "completion_cycle_id": cycle.completion_cycle_id,
                "completed_at": cycle.completed_at,
                "completion_evidence_kind": cycle.completion_evidence_kind,
                "completion_evidence_revision": cycle.completion_evidence_revision,
                "completion_evidence_reason": cycle.completion_evidence_reason,
                "external_revision_approved": int(
                    cycle.external_revision_approved
                ),
                "completion_commit_required": int(
                    cycle.completion_commit_required
                ),
                "completion_commit_hash": cycle.completion_commit_hash,
            },
            completion_cycle_id=cycle.completion_cycle_id,
        )
        bundle_basis = storage.read_native_completion_bundle_basis_locked(
            connection,
            project_id=str(basis["project_id"]),
            task_id=str(basis["task_id"]),
            cycle=cycle,
            completion_reference=completion_reference,
        )
        storage.persist_evidence_reference_locked(
            connection,
            reference=completion_reference,
        )
        plan = build_fixture_bundle_plan(
            basis=bundle_basis,
            cycle=cycle,
            identity=identity,
        )
        self.assertEqual(len(plan["storage_links"]), 5)
        self.assertEqual(len(plan["reference_ids"]), 5)
        links = tuple(
            storage.PreparedCriterionEvidenceLink(**value)
            for value in plan["storage_links"]
        )
        members = tuple(
            [
                storage.PreparedCompletionBundleMember(
                    project_id=str(basis["project_id"]),
                    task_id=str(basis["task_id"]),
                    completion_evidence_bundle_id=(
                        identity.completion_evidence_bundle_id
                    ),
                    member_kind="criterion_link",
                    ordinal=ordinal,
                    criterion_evidence_link_id=link.criterion_evidence_link_id,
                    evidence_reference_id=None,
                )
                for ordinal, link in enumerate(links)
            ]
            + [
                storage.PreparedCompletionBundleMember(
                    project_id=str(basis["project_id"]),
                    task_id=str(basis["task_id"]),
                    completion_evidence_bundle_id=(
                        identity.completion_evidence_bundle_id
                    ),
                    member_kind="evidence_reference",
                    ordinal=ordinal,
                    criterion_evidence_link_id=None,
                    evidence_reference_id=str(reference_id),
                )
                for ordinal, reference_id in enumerate(plan["reference_ids"])
            ]
        )
        findings = tuple(
            storage.PreparedCompletionFindingSnapshot(
                project_id=str(basis["project_id"]),
                task_id=str(basis["task_id"]),
                completion_evidence_bundle_id=identity.completion_evidence_bundle_id,
                ordinal=ordinal,
                **snapshot,
            )
            for ordinal, snapshot in enumerate(plan["finding_snapshots"])
        )
        bundle = storage.PreparedCompletionEvidenceBundle(
            completion_evidence_bundle_id=identity.completion_evidence_bundle_id,
            project_id=str(basis["project_id"]),
            task_id=str(basis["task_id"]),
            completion_cycle_id=identity.completion_cycle_id,
            cycle_ordinal=identity.saved_cycle_ordinal,
            source_schema_version=19,
            bundle_version=1,
            contract_revision=cycle.contract_revision,
            authority_snapshot_id=str(basis["authority_snapshot_id"]),
            acceptance_criterion_id=str(basis["acceptance_criterion_id"]),
            verification_criterion_id=str(basis["verification_criterion_id"]),
            target_kind="diff_fingerprint",
            target_value=labeled("a"),
            target_base_revision="",
            target_generation=1,
            target_capture_version=1,
            artifact_manifest_id=str(basis["artifact_manifest_id"]),
            verification_receipt_id=str(
                verification_receipt["verification_receipt_id"]
            ),
            omission_mask=int(plan["omission_mask"]),
            sealed_at=NOW,
            bundle_digest=str(plan["bundle_digest"]),
            payload_size_bytes=len(plan["payload_bytes"]),
            criterion_links=links,
            members=members,
            finding_snapshots=findings,
        )
        persisted = storage.insert_native_completion_cycle_locked(
            connection,
            project_id=str(basis["project_id"]),
            task_id=str(basis["task_id"]),
            task_projection=proposed,
            recorded_at=NOW,
            verification_expectation_digest=storage.verification_expectation_digest(
                "python -m unittest"
            ),
            verification_receipt_id=str(
                verification_receipt["verification_receipt_id"]
            ),
            verification_subject_basis_version=1,
            subject_authority_snapshot_id=str(basis["authority_snapshot_id"]),
            subject_verification_criterion_id=str(
                basis["verification_criterion_id"]
            ),
            completion_identity=identity,
            completion_bundle=bundle,
            prepared_cycle=cycle,
        )
        self.assertEqual(persisted, cycle)
        connection.execute(
            """
            UPDATE tasks
               SET status = 'done', completed_at = ?, updated_at = ?,
                   completion_evidence_kind = 'commit_not_required',
                   completion_evidence_revision = '',
                   completion_evidence_reason = '',
                   external_revision_approved = 0,
                   completion_commit_required = 0,
                   completion_commit_hash = ''
             WHERE project_id = ? AND task_id = ?
            """,
            (NOW, NOW, basis["project_id"], basis["task_id"]),
        )
        connection.execute(
            """
            INSERT INTO task_events(
              task_event_id, task_id, project_id, event_type, summary,
              created_at, completion_cycle_id
            ) VALUES (?, ?, ?, 'task_updated', 'Task completed', ?, ?)
            """,
            (
                "tg_event_" + "d" * 16,
                basis["task_id"],
                basis["project_id"],
                NOW,
                cycle.completion_cycle_id,
            ),
        )
        connection.commit()
        storage.validate_evidence_ledger_storage(connection)
        storage.validate_completion_cycle_storage(connection)
        storage.validate_completion_evidence_bundle_storage(connection)
        return basis, bytes(plan["payload_bytes"])

    @staticmethod
    def _resolution_values(
        basis: dict[str, object],
        suffix: str,
    ) -> dict[str, object]:
        return {
            "verification_runner_resolution_id": (
                "tg_verification_runner_resolution_" + suffix * 16
            ),
            **{
                name: basis[name]
                for name in (
                    "project_id",
                    "task_id",
                    "contract_revision",
                    "authority_snapshot_id",
                    "verification_criterion_id",
                    "verification_expectation_digest",
                    "verification_criterion_digest",
                    "artifact_manifest_id",
                )
            },
            "target_kind": "diff_fingerprint",
            "target_value": labeled("a"),
            "target_base_revision": None,
            "target_generation": 1,
            "target_capture_version": 1,
            "target_material_digest": labeled("c"),
            "plan_state": "ready",
            "plan_blob_object_id": None,
            "plan_raw_digest": None,
            "plan_id": None,
            "plan_version": None,
            "plan_semantic_digest": None,
            "selected_entry_digest": None,
            "coverage": "full",
            "step_count": 1,
            "runner_contract_version": 1,
            "runner_implementation_version": "taskgov-verification-runner/1",
            "runner_implementation_digest": labeled("d"),
            "runner_policy_digest": labeled("e"),
            "runtime_digest": None,
            "gate_eligibility_version": 0,
            "trigger": "review_target_set_v1",
            "route": "direct",
            "reason": None,
            "idempotency_digest": labeled("f"),
            "created_at": NOW,
        }

    @staticmethod
    def _insert_resolution(
        connection: sqlite3.Connection,
        values: dict[str, object],
    ) -> None:
        columns = tuple(values)
        connection.execute(
            "INSERT INTO verification_runner_resolutions("
            + ",".join(columns)
            + ") VALUES ("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(values[column] for column in columns),
        )

    def test_parent_triggers_cardinality_and_digest_adapter(self) -> None:
        db = self._fresh_v19("parents")
        storage.rehearse_schema20_storage(db)
        with closing(storage.connect(db)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            nullable = self._seed_target(
                connection,
                suffix="1",
                acceptance=None,
            )
            accepted = self._seed_target(
                connection,
                suffix="2",
                acceptance="storage acceptance",
            )
            for ordinal, basis in enumerate((nullable, accepted), start=1):
                values = self._resolution_values(basis, str(ordinal))
                projection = storage._verification_runner_resolution_digest_projection(
                    values
                )
                self.assertIsNone(projection["sandbox_provider"])
                self.assertIsNone(projection["sandbox_policy_digest"])
                values["idempotency_digest"] = resolution_idempotency_digest(
                    projection
                )
                self._insert_resolution(connection, values)

            resolution = self._resolution_values(nullable, "1")
            for suffix in ("3", "4"):
                attempt = {
                    "verification_runner_attempt_id": (
                        "tg_verification_runner_attempt_" + suffix * 16
                    ),
                    "project_id": nullable["project_id"],
                    "task_id": nullable["task_id"],
                    "target_generation": 1,
                    "gate_eligibility_version": 0,
                    "verification_runner_resolution_id": (
                        resolution["verification_runner_resolution_id"]
                    ),
                    "target_material_digest": labeled("c"),
                    "runner_implementation_digest": labeled("d"),
                    "attempt_digest": labeled("1"),
                    "intent_recorded_at": NOW,
                }
                attempt["attempt_digest"] = verification_runner_attempt_digest(
                    storage._verification_runner_attempt_digest_projection(attempt)
                )
                self.assertIsNone(
                    storage._verification_runner_attempt_digest_projection(attempt)[
                        "sandbox_instance_digest"
                    ]
                )
                columns = tuple(attempt)
                connection.execute(
                    "INSERT INTO verification_runner_attempts("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")",
                    tuple(attempt[column] for column in columns),
                )
                observation_id = (
                    "tg_verification_runner_observation_" + suffix * 16
                )
                connection.execute(
                    """
                    INSERT INTO verification_runner_observations(
                      verification_runner_observation_id, project_id, task_id,
                      target_generation, gate_eligibility_version,
                      verification_runner_resolution_id,
                      verification_runner_attempt_id,
                      runner_implementation_digest, route, launch_state,
                      outcome, reason, complete_plan, total_step_count,
                      completed_step_count, failed_step_ordinal, started_at,
                      finished_at, duration_ms, cpu_time_ms,
                      peak_job_memory_bytes, total_process_count,
                      sanitized_result_digest, created_at
                    ) VALUES (?, ?, ?, 1, 0, ?, ?, ?, 'direct', 'finished',
                              'pass', NULL, 1, 1, 1, NULL, ?, ?, 1,
                              NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        observation_id,
                        nullable["project_id"],
                        nullable["task_id"],
                        resolution["verification_runner_resolution_id"],
                        attempt["verification_runner_attempt_id"],
                        labeled("d"),
                        NOW,
                        NOW,
                        labeled("2"),
                        NOW,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO verification_runner_sandbox_events(
                      verification_runner_sandbox_event_id, project_id,
                      task_id, target_generation, verification_runner_attempt_id,
                      event_kind, event_digest, terminal_observation_id, created_at
                    ) VALUES (?, ?, ?, 1, ?, 'attempt_cleanup_succeeded', ?, ?, ?)
                    """,
                    (
                        "tg_verification_runner_sandbox_event_" + suffix * 16,
                        nullable["project_id"],
                        nullable["task_id"],
                        attempt["verification_runner_attempt_id"],
                        labeled("3"),
                        observation_id,
                        NOW,
                    ),
                )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_attempts"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_observations"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runner_sandbox_events"
                ).fetchone()[0],
                2,
            )
            for statement in (
                "UPDATE verification_runner_resolutions SET created_at = created_at",
                "DELETE FROM verification_runner_attempts",
                "UPDATE verification_runner_sandbox_events SET created_at = created_at",
                "DELETE FROM verification_runner_observations",
            ):
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "runner_storage_immutable",
                ):
                    connection.execute(statement)
            connection.rollback()

        for index, mode in enumerate(("wrong_kind", "missing_membership"), start=5):
            case_db = self._fresh_v19(f"parent-negative-{mode}")
            storage.rehearse_schema20_storage(case_db)
            with closing(storage.connect(case_db)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                basis = self._seed_target(
                    connection,
                    suffix=str(index),
                    acceptance=None,
                    invalid_acceptance=mode,
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    self._insert_resolution(
                        connection,
                        self._resolution_values(basis, str(index)),
                    )
                connection.rollback()

    def test_public_schema_remains_19(self) -> None:
        db = self._fresh_v19("public")
        self.assertEqual(storage.SCHEMA_VERSION, 19)
        with closing(storage.connect(db)) as connection:
            applied, warnings = storage.apply_migrations(connection)
            self.assertEqual((applied, warnings), ([], []))
            self.assertEqual(storage.current_schema_version(connection), 19)

        private_db = self._fresh_v19("private-viewer")
        storage.rehearse_schema20_storage(private_db)
        private_target = storage.resolve_database_target(
            repo=self.root / "private-viewer" / "repo",
            db=private_db,
            script_path=ROOT / "task-governance-tool" / "scripts" / "taskgov.py",
        )
        with closing(storage.connect_readonly(private_db)) as connection:
            with self.assertRaises(storage.StorageError) as raised:
                storage.validate_snapshot_database(connection, private_target)
            self.assertEqual(raised.exception.code, "migration_required")


if __name__ == "__main__":
    unittest.main()
