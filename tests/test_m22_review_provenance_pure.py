from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.review_provenance import (
        DIGEST_DOMAIN,
        INVALID_REVIEW_PROVENANCE_MESSAGE,
        REVIEW_PROVENANCE_FIELDS,
        ReviewProvenanceError,
        build_review_provenance_v1,
        legacy_review_provenance,
        normalize_review_provenance_input,
        project_review_provenance,
        validate_stored_review_provenance_v1,
    )
    from task_governance_tool.tasks import TaskValidationError
finally:
    sys.path.pop(0)


PROJECT_ID = "task-governance-tool-197f8f071dfc"
TASK_ID = "tg_task_88bfe19eb6cffe2e"
RECEIPT_ID = "tg_review_receipt_0123456789abcdef"
PROVENANCE_ID = "tg_review_provenance_fedcba9876543210"
TARGET = {
    "kind": "diff_fingerprint",
    "value": "sha256:" + "a" * 64,
    "base_revision": "",
    "generation": 7,
    "capture_version": 1,
}


def human_input(**overrides):
    values = {
        "receipt_kind": "independent",
        "reviewer_class": "human",
        "model_state": "not_applicable",
        "skill_state": "not_applicable",
        "context_relation": "external_context",
    }
    values.update(overrides)
    return normalize_review_provenance_input(**values)


def build_human(**overrides):
    normalized = human_input()
    values = {
        "project_id": PROJECT_ID,
        "task_id": TASK_ID,
        "review_receipt_id": RECEIPT_ID,
        "receipt_kind": "independent",
        "target": TARGET,
        "normalized_input": normalized,
        "review_provenance_id": PROVENANCE_ID,
    }
    values.update(overrides)
    return build_review_provenance_v1(**values)


class ReviewProvenancePureTests(unittest.TestCase):
    def assert_invalid(self, **values):
        with self.assertRaises(ReviewProvenanceError) as captured:
            normalize_review_provenance_input(**values)
        self.assertEqual(captured.exception.code, "invalid_review_evidence")
        self.assertEqual(
            captured.exception.message,
            INVALID_REVIEW_PROVENANCE_MESSAGE,
        )

    def test_closed_case_matrix_accepts_all_supported_classes(self):
        cases = (
            {
                "reviewer_class": "human",
                "model_state": "not_applicable",
                "skill_state": "not_applicable",
            },
            {
                "reviewer_class": "deterministic_tool",
                "model_state": "not_applicable",
                "skill_state": "not_applicable",
            },
            {
                "reviewer_class": "llm",
                "model_state": "declared",
                "declared_model_id": "openai/gpt-5.6",
                "skill_state": "not_used",
            },
            {
                "reviewer_class": "llm",
                "model_state": "unknown",
                "skill_state": "declared",
                "declared_skill_id": "review/skill-v1",
                "declared_skill_version": "1.0+local",
            },
            {
                "reviewer_class": "hybrid",
                "model_state": "declared",
                "declared_model_id": "model:1",
                "skill_state": "unknown",
            },
            {
                "reviewer_class": "unknown",
                "model_state": "unknown",
                "skill_state": "unknown",
            },
        )
        for case in cases:
            with self.subTest(case=case):
                normalized = normalize_review_provenance_input(
                    receipt_kind="independent",
                    context_relation="unknown",
                    **case,
                )
                self.assertEqual(normalized["reviewer_class"], case["reviewer_class"])

    def test_cross_field_matrix_rejects_missing_and_extra_declarations(self):
        invalid_cases = (
            {
                "reviewer_class": "human",
                "model_state": "unknown",
                "skill_state": "not_applicable",
            },
            {
                "reviewer_class": "llm",
                "model_state": "declared",
                "skill_state": "not_used",
            },
            {
                "reviewer_class": "llm",
                "model_state": "unknown",
                "declared_model_id": "model-1",
                "skill_state": "not_used",
            },
            {
                "reviewer_class": "hybrid",
                "model_state": "unknown",
                "skill_state": "declared",
                "declared_skill_id": "skill-1",
            },
            {
                "reviewer_class": "unknown",
                "model_state": "unknown",
                "skill_state": "not_used",
            },
        )
        for case in invalid_cases:
            with self.subTest(case=case):
                self.assert_invalid(
                    receipt_kind="independent",
                    context_relation="unknown",
                    **case,
                )

    def test_not_required_forbids_options_and_projects_null(self):
        self.assertIsNone(
            normalize_review_provenance_input(receipt_kind="not_required")
        )
        self.assertIsNone(legacy_review_provenance("not_required"))
        self.assertIsNone(
            project_review_provenance(
                receipt_kind="not_required",
                basis_version=0,
                provenance=None,
            )
        )
        self.assert_invalid(
            receipt_kind="not_required",
            reviewer_class="human",
        )

    def test_privacy_precedes_enum_bound_duplicate_and_matrix_validation(self):
        cases = (
            {
                "reviewer_class": "invalid",
                "model_state": "invalid",
                "skill_state": "invalid",
                "context_relation": "invalid",
                "declared_model_id": "Authorization: Bearer sk-secret-value",
            },
            {
                "reviewer_class": "human",
                "model_state": "not_applicable",
                "skill_state": "not_applicable",
                "context_relation": "unknown",
                "review_profiles": [
                    "general",
                    "general",
                    "invalid",
                    "privacy_safety",
                    "System prompt: hidden instructions",
                ],
            },
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(TaskValidationError) as captured:
                    normalize_review_provenance_input(
                        receipt_kind="independent",
                        **case,
                    )
                self.assertEqual(captured.exception.code, "privacy_rejected")
                self.assertNotIn("sk-secret-value", captured.exception.message)
                self.assertNotIn("hidden instructions", captured.exception.message)

    def test_collections_enforce_counts_duplicates_and_canonical_order(self):
        normalized = human_input(
            review_profiles=["verification", "general", "privacy_safety"],
            review_lenses=["performance", "correctness", "privacy"],
            method_codes=["runtime_observation", "source_inspection"],
        )
        self.assertEqual(
            normalized["review_profiles"],
            ["general", "verification", "privacy_safety"],
        )
        self.assertEqual(
            normalized["review_lenses"],
            ["correctness", "privacy", "performance"],
        )
        self.assertEqual(
            normalized["method_codes"],
            ["source_inspection", "runtime_observation"],
        )
        for field, value in (
            ("review_profiles", ["general"] * 2),
            (
                "review_profiles",
                [
                    "general",
                    "authority_contract",
                    "implementation",
                    "verification",
                    "privacy_safety",
                ],
            ),
            ("review_lenses", ["correctness"] * 9),
            ("method_codes", ["diff_inspection"] * 9),
        ):
            with self.subTest(field=field):
                values = {
                    "receipt_kind": "independent",
                    "reviewer_class": "human",
                    "model_state": "not_applicable",
                    "skill_state": "not_applicable",
                    "context_relation": "unknown",
                    field: value,
                }
                self.assert_invalid(**values)

    def test_declared_identifier_grammar_and_boundaries(self):
        valid_identifier = "a" + "x" * 127
        valid_version = "v" + "1" * 63
        accepted = normalize_review_provenance_input(
            receipt_kind="independent",
            reviewer_class="llm",
            model_state="declared",
            declared_model_id=valid_identifier,
            skill_state="declared",
            declared_skill_id=valid_identifier,
            declared_skill_version=valid_version,
            context_relation="fresh_context",
        )
        self.assertEqual(accepted["declared_model_id"], valid_identifier)
        self.assertEqual(accepted["declared_skill_version"], valid_version)
        for field, value in (
            ("declared_model_id", "a" + "x" * 128),
            ("declared_model_id", " model"),
            ("declared_model_id", "モデル"),
            ("declared_skill_version", "v" + "1" * 64),
            ("declared_skill_version", "v/1"),
        ):
            with self.subTest(field=field, value=value):
                values = {
                    "receipt_kind": "independent",
                    "reviewer_class": "llm",
                    "model_state": "declared",
                    "declared_model_id": "model-1",
                    "skill_state": "declared",
                    "declared_skill_id": "skill-1",
                    "declared_skill_version": "1.0",
                    "context_relation": "same_context",
                    field: value,
                }
                self.assert_invalid(**values)

    def test_v1_object_has_exact_keys_and_domain_separated_digest(self):
        provenance = build_human()
        self.assertEqual(tuple(provenance), REVIEW_PROVENANCE_FIELDS)
        self.assertEqual(provenance["review_provenance_id"], PROVENANCE_ID)
        self.assertEqual(provenance["provenance_version"], 1)
        self.assertEqual(provenance["assurance_class"], "bound_attestation")
        self.assertEqual(provenance["producer_class"], "trusted_caller")
        self.assertRegex(provenance["digest"], r"^sha256:[0-9a-f]{64}$")
        digest_payload = {
            "project_id": PROJECT_ID,
            "task_id": TASK_ID,
            "review_receipt_id": RECEIPT_ID,
            "receipt_kind": "independent",
            "target": TARGET,
            **{
                field: provenance[field]
                for field in REVIEW_PROVENANCE_FIELDS[1:-1]
            },
        }
        expected = "sha256:" + hashlib.sha256(
            DIGEST_DOMAIN
            + json.dumps(
                digest_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(provenance["digest"], expected)

    def test_v1_builder_rejects_noncanonical_or_matrix_invalid_input(self):
        noncanonical = human_input(
            review_profiles=["general", "verification"],
        )
        noncanonical["review_profiles"] = ["verification", "general"]
        invalid_matrix = human_input()
        invalid_matrix["model_state"] = "unknown"
        for normalized in (noncanonical, invalid_matrix):
            with self.subTest(normalized=normalized):
                with self.assertRaises(ReviewProvenanceError):
                    build_review_provenance_v1(
                        project_id=PROJECT_ID,
                        task_id=TASK_ID,
                        review_receipt_id=RECEIPT_ID,
                        receipt_kind="independent",
                        target=TARGET,
                        normalized_input=normalized,
                        review_provenance_id=PROVENANCE_ID,
                    )

    def test_digest_is_independent_of_input_code_order_and_random_id(self):
        first = build_review_provenance_v1(
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            review_receipt_id=RECEIPT_ID,
            receipt_kind="independent",
            target=TARGET,
            normalized_input=human_input(
                review_profiles=["verification", "general"],
            ),
            review_provenance_id=PROVENANCE_ID,
        )
        second = build_review_provenance_v1(
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            review_receipt_id=RECEIPT_ID,
            receipt_kind="independent",
            target=TARGET,
            normalized_input=human_input(
                review_profiles=["general", "verification"],
            ),
            review_provenance_id="tg_review_provenance_0000000000000000",
        )
        self.assertEqual(first["digest"], second["digest"])

    def test_legacy_v0_is_exact_and_distinct_from_explicit_unknown(self):
        legacy = legacy_review_provenance("independent")
        self.assertEqual(tuple(legacy), REVIEW_PROVENANCE_FIELDS)
        self.assertEqual(legacy["provenance_version"], 0)
        for field in REVIEW_PROVENANCE_FIELDS[0:1] + REVIEW_PROVENANCE_FIELDS[2:12] + ("digest",):
            self.assertIsNone(legacy[field])
        self.assertEqual(legacy["assurance_class"], "legacy_unknown")
        self.assertEqual(legacy["producer_class"], "legacy_migration")
        explicit_unknown = build_review_provenance_v1(
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            review_receipt_id=RECEIPT_ID,
            receipt_kind="independent",
            target=TARGET,
            normalized_input=normalize_review_provenance_input(
                receipt_kind="independent",
                reviewer_class="unknown",
                model_state="unknown",
                skill_state="unknown",
                context_relation="unknown",
            ),
            review_provenance_id=PROVENANCE_ID,
        )
        self.assertEqual(explicit_unknown["review_profiles"], [])
        self.assertNotEqual(explicit_unknown, legacy)

    def test_stored_validator_accepts_exact_v1_and_rejects_corruption(self):
        provenance = build_human()
        self.assertEqual(
            validate_stored_review_provenance_v1(
                provenance,
                project_id=PROJECT_ID,
                task_id=TASK_ID,
                review_receipt_id=RECEIPT_ID,
                receipt_kind="independent",
                target=TARGET,
            ),
            provenance,
        )
        corruptions = []
        for field, value in (
            ("digest", "sha256:" + "0" * 64),
            ("producer_class", "taskgov_core"),
            ("review_profiles", ["verification", "general"]),
            ("reviewer_class", "human"),
        ):
            corrupted = copy.deepcopy(provenance)
            corrupted[field] = value
            if field == "reviewer_class":
                corrupted["model_state"] = "unknown"
            corruptions.append(corrupted)
        extra = copy.deepcopy(provenance)
        extra["extra"] = None
        corruptions.append(extra)
        missing = copy.deepcopy(provenance)
        missing.pop("digest")
        corruptions.append(missing)
        for corrupted in corruptions:
            with self.subTest(corrupted=corrupted):
                with self.assertRaises(ReviewProvenanceError) as captured:
                    validate_stored_review_provenance_v1(
                        corrupted,
                        project_id=PROJECT_ID,
                        task_id=TASK_ID,
                        review_receipt_id=RECEIPT_ID,
                        receipt_kind="independent",
                        target=TARGET,
                    )
                self.assertEqual(
                    captured.exception.message,
                    INVALID_REVIEW_PROVENANCE_MESSAGE,
                )

    def test_stored_validator_accepts_mapping_order_but_projects_canonical_order(self):
        provenance = build_human()
        reordered = {
            field: provenance[field]
            for field in reversed(REVIEW_PROVENANCE_FIELDS)
        }
        validated = validate_stored_review_provenance_v1(
            reordered,
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            review_receipt_id=RECEIPT_ID,
            receipt_kind="independent",
            target={
                field: TARGET[field]
                for field in reversed(tuple(TARGET))
            },
        )
        self.assertEqual(tuple(validated), REVIEW_PROVENANCE_FIELDS)

    def test_stored_privacy_fault_is_structural_sanitized_error(self):
        provenance = build_human()
        provenance["context_relation"] = "System prompt: hidden instructions"
        with self.assertRaises(ReviewProvenanceError) as captured:
            validate_stored_review_provenance_v1(
                provenance,
                project_id=PROJECT_ID,
                task_id=TASK_ID,
                review_receipt_id=RECEIPT_ID,
                receipt_kind="independent",
                target=TARGET,
            )
        self.assertNotIn("hidden instructions", captured.exception.message)

    def test_public_union_rejects_invalid_basis_kind_relations(self):
        provenance = build_human()
        projected = project_review_provenance(
            receipt_kind="independent",
            basis_version=1,
            provenance=provenance,
            project_id=PROJECT_ID,
            task_id=TASK_ID,
            review_receipt_id=RECEIPT_ID,
            target=TARGET,
        )
        self.assertEqual(projected, provenance)
        legacy = project_review_provenance(
            receipt_kind="self_review_fallback",
            basis_version=0,
            provenance=None,
        )
        self.assertEqual(legacy["provenance_version"], 0)
        invalid_cases = (
            {
                "receipt_kind": "independent",
                "basis_version": 0,
                "provenance": provenance,
            },
            {
                "receipt_kind": "independent",
                "basis_version": 1,
                "provenance": None,
            },
            {
                "receipt_kind": "not_required",
                "basis_version": 1,
                "provenance": provenance,
            },
        )
        for values in invalid_cases:
            with self.subTest(values=values):
                with self.assertRaises(ReviewProvenanceError):
                    project_review_provenance(**values)

    def test_canonical_digest_rejects_lone_surrogate_context(self):
        with self.assertRaises(ReviewProvenanceError):
            build_human(project_id="project\ud800")


if __name__ == "__main__":
    unittest.main()
