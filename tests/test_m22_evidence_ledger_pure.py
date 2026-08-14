import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.evidence_ledger import (
        AUTHORITY_SNAPSHOT_DOMAIN,
        CONTRACT_CRITERION_DOMAIN,
        EVIDENCE_REFERENCE_DOMAIN,
        VERIFICATION_EXPECTATION_DIGEST_DOMAIN,
        AuthorityBasis,
        EvidenceLedgerError,
        EvidenceSource,
        ProducerAttribution,
        TargetCaptureBinding,
        authority_snapshot_basis_digest,
        build_authority_snapshot,
        build_contract_criterion,
        build_evidence_reference,
        canonical_json_bytes,
        criteria_for_authority_basis,
        evidence_reference_digest,
        require_capture_v1,
        verification_expectation_digest,
    )
    from task_governance_tool.verification_runner import (
        verification_runner_policy_digest,
    )
finally:
    sys.path.pop(0)


SNAPSHOT_ID = "tg_authority_snapshot_0123456789abcdef"
ACCEPTANCE_ID = "tg_contract_criterion_1111111111111111"
VERIFICATION_ID = "tg_contract_criterion_2222222222222222"
PROJECT_ID = "project-1"
TASK_ID = "tg_task_0123456789abcdef"


def basis(**changes):
    values = {
        "task_title": "Exact title",
        "task_description": "Description",
        "review_tier": 2,
        "verification": "  pytest -q\n",
        "contract_revision": 3,
        "contract_state": "contract_specified",
        "contract_scope": "scope",
        "contract_acceptance": "  exact acceptance  ",
        "contract_constraints": "constraints",
        "contract_authority_ref": "docs/specification.md#anchor",
    }
    values.update(changes)
    return AuthorityBasis(**values)


def binding(**changes):
    values = {
        "target_kind": "git_snapshot",
        "target_value": "sha256:" + "a" * 64,
        "target_base_revision": "b" * 40,
        "target_generation": 4,
        "authority_snapshot_id": SNAPSHOT_ID,
        "acceptance_criterion_id": ACCEPTANCE_ID,
        "verification_criterion_id": VERIFICATION_ID,
    }
    values.update(changes)
    return TargetCaptureBinding(**values)


def authority_values(
    exact,
    *,
    producer_class="taskgov_core",
    acceptance_criterion_id=ACCEPTANCE_ID,
    verification_criterion_id=VERIFICATION_ID,
):
    return {
        "project_id": PROJECT_ID,
        "task_id": TASK_ID,
        **exact.canonical_value(),
        "verification_digest": verification_expectation_digest(
            exact.verification
        ),
        "acceptance_criterion_id": acceptance_criterion_id,
        "verification_criterion_id": verification_criterion_id,
        "producer_class": producer_class,
        "producer_version": 1,
    }


class AuthoritySnapshotTests(unittest.TestCase):
    def test_snapshot_digest_matches_exact_runtime_basis(self):
        exact = basis()
        first = build_authority_snapshot(
            authority_snapshot_id=SNAPSHOT_ID,
            generation=7,
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            basis=exact,
            acceptance_criterion_id=ACCEPTANCE_ID,
            verification_criterion_id=VERIFICATION_ID,
        )
        second = build_authority_snapshot(
            authority_snapshot_id="tg_authority_snapshot_fedcba9876543210",
            generation=8,
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            basis=exact,
            acceptance_criterion_id=ACCEPTANCE_ID,
            verification_criterion_id=VERIFICATION_ID,
        )
        values = authority_values(exact)
        expected = "sha256:" + hashlib.sha256(
            AUTHORITY_SNAPSHOT_DOMAIN
            + json.dumps(
                values,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(first.basis_digest, expected)
        self.assertEqual(second.basis_digest, expected)
        self.assertEqual(
            first.verification_digest,
            hashlib.sha256(
                VERIFICATION_EXPECTATION_DIGEST_DOMAIN
                + "  pytest -q\n".encode()
            ).hexdigest(),
        )

    def test_authority_basis_accepts_each_exact_field_limit(self):
        for field, limit in (
            ("task_title", 200),
            ("task_description", 4_000),
            ("verification", 1_000),
            ("contract_scope", 4_000),
            ("contract_acceptance", 4_000),
            ("contract_constraints", 2_000),
            ("contract_authority_ref", 500),
        ):
            with self.subTest(field=field):
                value = "界" * limit
                self.assertEqual(getattr(basis(**{field: value}), field), value)

    def test_authority_basis_rejects_each_field_limit_plus_one(self):
        for field, limit in (
            ("task_title", 200),
            ("task_description", 4_000),
            ("verification", 1_000),
            ("contract_scope", 4_000),
            ("contract_acceptance", 4_000),
            ("contract_constraints", 2_000),
            ("contract_authority_ref", 500),
        ):
            with self.subTest(field=field), self.assertRaises(
                EvidenceLedgerError
            ) as raised:
                basis(**{field: "界" * (limit + 1)})
            self.assertEqual(raised.exception.code, "evidence_ledger_inconsistent")
            self.assertEqual(
                str(raised.exception),
                "stored evidence ledger is inconsistent",
            )

    def test_verification_digest_preserves_exact_utf8_and_whitespace(self):
        exact = " \t検証対象\n"
        expected = hashlib.sha256(
            VERIFICATION_EXPECTATION_DIGEST_DOMAIN + exact.encode("utf-8")
        ).hexdigest()
        self.assertEqual(verification_expectation_digest(exact), expected)
        self.assertRegex(expected, r"^[0-9a-f]{64}$")
        self.assertNotEqual(expected, hashlib.sha256(exact.encode("utf-8")).hexdigest())

    def test_authority_basis_digest_uses_exact_runtime_keyset(self):
        exact = basis(verification=" \t検証対象\n")
        values = authority_values(exact)
        expected = "sha256:" + hashlib.sha256(
            AUTHORITY_SNAPSHOT_DOMAIN
            + json.dumps(
                values,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(authority_snapshot_basis_digest(values), expected)

        for invalid in (
            {key: value for key, value in values.items() if key != "task_id"},
            {**values, "unexpected": "value"},
            {**values, "verification_digest": "0" * 64},
        ):
            with self.subTest(keys=tuple(sorted(invalid))), self.assertRaises(
                EvidenceLedgerError
            ) as raised:
                authority_snapshot_basis_digest(invalid)
            self.assertEqual(raised.exception.code, "evidence_ledger_inconsistent")
            self.assertEqual(
                str(raised.exception),
                "stored evidence ledger is inconsistent",
            )

    def test_authority_basis_digest_enforces_criterion_presence_matrix(self):
        revision_zero = basis(
            contract_revision=0,
            contract_state="contract_unspecified",
            contract_scope="",
            contract_acceptance="",
            contract_constraints="",
            contract_authority_ref="",
        )
        trimmed_empty_verification = basis(verification=" \t\n")
        valid = (
            authority_values(
                revision_zero,
                acceptance_criterion_id=None,
            ),
            authority_values(
                basis(),
                acceptance_criterion_id=ACCEPTANCE_ID,
            ),
            authority_values(
                trimmed_empty_verification,
                verification_criterion_id=None,
            ),
            authority_values(
                basis(),
                verification_criterion_id=VERIFICATION_ID,
            ),
        )
        for values in valid:
            with self.subTest(valid=values):
                self.assertRegex(
                    authority_snapshot_basis_digest(values),
                    r"^sha256:[0-9a-f]{64}$",
                )

        invalid = (
            authority_values(
                revision_zero,
                acceptance_criterion_id=ACCEPTANCE_ID,
            ),
            authority_values(
                basis(),
                acceptance_criterion_id=None,
            ),
            authority_values(
                trimmed_empty_verification,
                verification_criterion_id=VERIFICATION_ID,
            ),
            authority_values(
                basis(),
                verification_criterion_id=None,
            ),
        )
        for values in invalid:
            with self.subTest(invalid=values), self.assertRaises(
                EvidenceLedgerError
            ) as raised:
                authority_snapshot_basis_digest(values)
            self.assertEqual(
                raised.exception.code,
                "evidence_ledger_inconsistent",
            )
            self.assertEqual(
                str(raised.exception),
                "stored evidence ledger is inconsistent",
            )

    def test_whole_field_criteria_preserve_whitespace_and_use_domain_bytes(self):
        exact = basis()
        acceptance, verification = criteria_for_authority_basis(
            exact,
            acceptance_criterion_id=ACCEPTANCE_ID,
            verification_criterion_id=VERIFICATION_ID,
        )
        self.assertEqual(acceptance.criterion_text, "  exact acceptance  ")
        expected = "sha256:" + hashlib.sha256(
            CONTRACT_CRITERION_DOMAIN
            + b"acceptance\0"
            + "  exact acceptance  ".encode()
        ).hexdigest()
        self.assertEqual(acceptance.digest, expected)
        self.assertEqual(verification.criterion_text, "  pytest -q\n")

    def test_criterion_kind_limits_accept_max_and_reject_max_plus_one(self):
        for criterion_kind, criterion_id, limit in (
            ("acceptance", ACCEPTANCE_ID, 4_000),
            ("verification", VERIFICATION_ID, 1_000),
        ):
            with self.subTest(kind=criterion_kind):
                exact = "界" * limit
                criterion = build_contract_criterion(
                    criterion_id=criterion_id,
                    criterion_kind=criterion_kind,
                    criterion_text=exact,
                )
                self.assertEqual(criterion.criterion_text, exact)
                with self.assertRaises(EvidenceLedgerError) as raised:
                    build_contract_criterion(
                        criterion_id=criterion_id,
                        criterion_kind=criterion_kind,
                        criterion_text=exact + "界",
                    )
                self.assertEqual(
                    raised.exception.code,
                    "evidence_ledger_inconsistent",
                )
                self.assertEqual(
                    str(raised.exception),
                    "stored evidence ledger is inconsistent",
                )

    def test_revision_zero_and_trimmed_empty_verification_omit_criteria(self):
        exact = basis(
            verification=" \t\n",
            contract_revision=0,
            contract_state="contract_unspecified",
            contract_scope="",
            contract_acceptance="",
            contract_constraints="",
            contract_authority_ref="",
        )
        self.assertEqual(
            criteria_for_authority_basis(
                exact,
                acceptance_criterion_id=None,
                verification_criterion_id=None,
            ),
            (None, None),
        )
        snapshot = build_authority_snapshot(
            authority_snapshot_id=SNAPSHOT_ID,
            generation=1,
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            basis=exact,
            acceptance_criterion_id=None,
            verification_criterion_id=None,
            producer_class="legacy_migration",
        )
        self.assertIn(b'"verification":" \\t\\n"', canonical_json_bytes(exact.canonical_value()))
        self.assertEqual(snapshot.producer_class, "legacy_migration")

    def test_criterion_rejects_empty_and_invalid_id_with_fixed_error(self):
        for kwargs in (
            {
                "criterion_id": ACCEPTANCE_ID,
                "criterion_kind": "acceptance",
                "criterion_text": " \n",
            },
            {
                "criterion_id": "bad-id",
                "criterion_kind": "acceptance",
                "criterion_text": "ok",
            },
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(EvidenceLedgerError) as raised:
                build_contract_criterion(**kwargs)
            self.assertEqual(raised.exception.code, "evidence_ledger_inconsistent")
            self.assertEqual(str(raised.exception), "stored evidence ledger is inconsistent")


class EvidenceReferenceTests(unittest.TestCase):
    def test_runner_observation_is_machine_observed_with_closed_projection(self):
        observation_id = "tg_verification_runner_observation_3333333333333333"
        projection = {
            "observation_id": observation_id,
            "gate_eligibility_version": 0,
            "route": "m21_fallback",
            "reason": "plan_absent",
            "outcome": "not_run",
            "launch_state": "no_launch",
            "complete_plan": 0,
            "total_step_count": 0,
            "completed_step_count": 0,
            "failed_step_ordinal": None,
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:00Z",
            "duration_ms": 0,
            "cpu_time_ms": None,
            "peak_job_memory_bytes": None,
            "total_process_count": None,
            "plan_blob_object_id": None,
            "plan_raw_digest": None,
            "plan_id": None,
            "plan_version": None,
            "plan_semantic_digest": None,
            "runner_implementation_version": "taskgov-verification-runner/1",
            "runner_implementation_digest": "sha256:" + "f" * 64,
            "runner_policy_digest": verification_runner_policy_digest(),
            "sandbox_provider": None,
            "sandbox_policy_digest": None,
            "runtime_digest": None,
            "sanitized_result_digest": "sha256:" + "4" * 64,
        }
        source = EvidenceSource(
            source_kind="runner_observation",
            source_state="recorded",
            source_id=observation_id,
            source_projection=projection,
        )
        result = build_evidence_reference(
            source=source,
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            contract_revision=3,
            binding=binding(),
        )
        self.assertEqual(
            (result.attribution.assurance_class, result.attribution.producer_class),
            ("machine_observed", "verification_runner"),
        )
        self.assertEqual(result.attribution.producer_version, 1)
        self.assertTrue(result.digest.startswith("sha256:"))

        with self.assertRaises(EvidenceLedgerError):
            EvidenceSource(
                source_kind="runner_observation",
                source_state="recorded",
                source_id=observation_id,
                source_projection={**projection, "stdout": "forbidden"},
            )

        uncertain_cleanup = {
            **projection,
            "complete_plan": 0,
            "completed_step_count": 1,
            "cpu_time_ms": None,
            "duration_ms": 0,
            "failed_step_ordinal": None,
            "finished_at": projection["started_at"],
            "launch_state": "launch_uncertain",
            "outcome": "sandbox_cleanup_failed",
            "peak_job_memory_bytes": None,
            "total_process_count": None,
            "reason": "sandbox_cleanup_failed",
            "route": "blocked",
            "total_step_count": 2,
        }
        with self.assertRaises(EvidenceLedgerError) as raised:
            EvidenceSource(
                source_kind="runner_observation",
                source_state="recorded",
                source_id=observation_id,
                source_projection=uncertain_cleanup,
            )
        self.assertEqual(raised.exception.code, "evidence_ledger_inconsistent")
        self.assertEqual(
            str(raised.exception),
            "stored evidence ledger is inconsistent",
        )

        invalid_changes = (
            {"runner_policy_digest": "sha256:" + "9" * 64},
            {
                "complete_plan": 0,
                "failed_step_ordinal": 1,
                "outcome": "fail",
                "reason": "timeout",
            },
            {
                "complete_plan": 0,
                "failed_step_ordinal": None,
                "outcome": "fail",
                "reason": "step_nonzero",
            },
            {
                "complete_plan": 0,
                "failed_step_ordinal": 1,
                "outcome": "process_error",
                "reason": "process_create_failed",
            },
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(EvidenceLedgerError):
                    EvidenceSource(
                        source_kind="runner_observation",
                        source_state="recorded",
                        source_id=observation_id,
                        source_projection={**projection, **changes},
                    )

    def test_closed_manifest_dispatch_and_exact_reference_digest(self):
        source = EvidenceSource(
            source_kind="artifact_manifest",
            source_state="complete_git",
            source_id="tg_artifact_manifest_3333333333333333",
            source_projection={
                "artifact_manifest_id": "tg_artifact_manifest_3333333333333333",
                "state": "complete_git",
                "object_format": "sha1",
                "comparison_base": "b" * 40,
                "entry_count": 2,
                "digest": "sha256:" + "c" * 64,
                "omission_code": None,
            },
        )
        result = build_evidence_reference(
            source=source,
            project_id="project-1",
            task_id="tg_task_0123456789abcdef",
            contract_revision=3,
            binding=binding(),
        )
        self.assertEqual(result.attribution.assurance_class, "machine_observed")
        self.assertEqual(result.attribution.producer_class, "taskgov_git")
        self.assertTrue(result.digest.startswith("sha256:"))
        expected_value = {
            "acceptance_criterion_id": ACCEPTANCE_ID,
            "assurance_class": "machine_observed",
            "authority_snapshot_id": SNAPSHOT_ID,
            "completion_cycle_id": None,
            "contract_revision": 3,
            "producer_class": "taskgov_git",
            "producer_version": 1,
            "project_id": "project-1",
            "source_id": source.source_id,
            "source_kind": "artifact_manifest",
            "source_projection": dict(source.source_projection),
            "source_state": "complete_git",
            "target_base_revision": "b" * 40,
            "target_generation": 4,
            "target_kind": "git_snapshot",
            "target_value": "sha256:" + "a" * 64,
            "task_id": "tg_task_0123456789abcdef",
            "verification_criterion_id": VERIFICATION_ID,
        }
        expected = "sha256:" + hashlib.sha256(
            EVIDENCE_REFERENCE_DOMAIN + canonical_json_bytes(expected_value)
        ).hexdigest()
        self.assertEqual(result.digest, expected)

    def test_opaque_dispatch_diff_and_external_are_distinct(self):
        for target_kind, assurance, producer in (
            ("diff_fingerprint", "bound_attestation", "trusted_caller"),
            ("external_revision", "external_reference", "external_system"),
        ):
            source = EvidenceSource(
                source_kind="artifact_manifest",
                source_state="opaque_target",
                source_id="tg_artifact_manifest_4444444444444444",
                source_projection={
                    "artifact_manifest_id": "tg_artifact_manifest_4444444444444444",
                    "state": "opaque_target",
                    "target_kind": target_kind,
                    "digest": "sha256:" + "d" * 64,
                    "omission_code": "artifact_content_not_observed",
                },
            )
            result = build_evidence_reference(
                source=source,
                project_id="project-1",
                task_id="tg_task_0123456789abcdef",
                contract_revision=3,
                binding=binding(
                    target_kind=target_kind,
                    target_value="external-value" if target_kind == "external_revision" else "sha256:" + "e" * 64,
                    target_base_revision="",
                ),
            )
            self.assertEqual(
                (result.attribution.assurance_class, result.attribution.producer_class),
                (assurance, producer),
            )

    def test_caller_cannot_upgrade_attribution_or_enable_reserved_source(self):
        source = EvidenceSource(
            source_kind="review_finding",
            source_state="recorded",
            source_id="tg_review_finding_5555555555555555",
            source_projection={
                "review_finding_id": "tg_review_finding_5555555555555555",
                "review_receipt_id": "tg_review_receipt_6666666666666666",
                "severity": "high",
                "summary": "original",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )
        with self.assertRaises(EvidenceLedgerError):
            evidence_reference_digest(
                source=source,
                project_id="project-1",
                task_id="tg_task_0123456789abcdef",
                contract_revision=3,
                binding=binding(),
                completion_cycle_id=None,
                attribution=ProducerAttribution("llm_derived", "batch_analyzer"),
            )
        with self.assertRaises(EvidenceLedgerError):
            EvidenceSource(
                source_kind="derived_analysis",
                source_state="recorded",
                source_id="tg_derived_analysis_7777777777777777",
                source_projection={},
            )

    def test_finding_projection_rejects_mutable_resolution_fields(self):
        with self.assertRaises(EvidenceLedgerError):
            EvidenceSource(
                source_kind="review_finding",
                source_state="recorded",
                source_id="tg_review_finding_5555555555555555",
                source_projection={
                    "review_finding_id": "tg_review_finding_5555555555555555",
                    "review_receipt_id": "tg_review_receipt_6666666666666666",
                    "severity": "high",
                    "summary": "original",
                    "created_at": "2026-01-01T00:00:00Z",
                    "status": "resolved",
                },
            )

    def test_capture_zero_uses_only_fixed_stale_error(self):
        with self.assertRaises(EvidenceLedgerError) as raised:
            require_capture_v1(0)
        self.assertEqual(raised.exception.code, "evidence_basis_stale")
        self.assertEqual(str(raised.exception), "current evidence basis must be captured again")


if __name__ == "__main__":
    unittest.main()
