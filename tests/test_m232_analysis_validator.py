from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.analysis_contracts import (  # noqa: E402
    build_descriptor,
    default_recipe,
)
from task_governance_tool.analysis_packet import (  # noqa: E402
    FIXED_PROMPT_DIGEST,
    build_analysis_packet,
)
from task_governance_tool.analysis_validator import (  # noqa: E402
    ADAPTER_OUTPUT_MAX_BYTES,
    AnalysisValidationError,
    build_analysis_report,
    validate_adapter_output,
    validate_recovery_report_document,
    validate_report_document,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    EvidenceConsumerError,
    ValidatedEvidenceSource,
)
from tests.m23_test_support import (  # noqa: E402
    BUNDLE_DOMAIN,
    domain_digest,
    reference_json_bytes,
    refresh_bundle_seals as _refresh_bundle_seals,
    valid_native_payload,
    v1_native_payload as v1_payload,
)


OUTPUT_DOMAIN = b"taskgov-analysis-output-v1\0"
REPORT_DOMAIN = b"taskgov-analysis-report-v1\0"
REPORT_ID_DOMAIN = b"taskgov-analysis-report-id-v1\0"
def _digest(byte: str) -> str:
    return "sha256:" + byte * 64


def _reference(
    payload: dict[str, object],
    *,
    suffix: str,
    source_kind: str,
    source_state: str,
    source_id: str,
    assurance_class: str,
    producer_class: str,
    completion_cycle_id: str | None = None,
) -> dict[str, object]:
    template = deepcopy(payload["evidence_references"][0])
    template.update(
        {
            "evidence_reference_id": "tg_evidence_reference_" + suffix * 16,
            "source_kind": source_kind,
            "source_state": source_state,
            "source_id": source_id,
            "assurance_class": assurance_class,
            "producer_class": producer_class,
            "completion_cycle_id": completion_cycle_id,
            "digest": _digest(suffix),
        }
    )
    return template


def _reference_order(row: dict[str, object]) -> tuple[int, bytes, bytes]:
    order = {
        "artifact_manifest": 0,
        "verification_receipt": 1,
        "review_receipt": 2,
        "review_finding": 3,
        "completion_evidence": 4,
        "derived_analysis": 5,
        "runner_observation": 6,
    }
    return (
        order[row["source_kind"]],
        row["source_id"].encode("utf-8"),
        row["evidence_reference_id"].encode("utf-8"),
    )


def native_payload() -> dict[str, object]:
    return valid_native_payload()


def native_job(
    *,
    payload: dict[str, object] | None = None,
    optional: bool = False,
):
    selected = native_payload() if payload is None else deepcopy(payload)
    _refresh_bundle_seals(selected)
    bundle_digest = "sha256:" + hashlib.sha256(
        BUNDLE_DOMAIN + reference_json_bytes(selected)
    ).hexdigest()
    envelope = {
        "bundle_digest": bundle_digest,
        "format_version": 1,
        "payload": selected,
    }
    bundle_document = reference_json_bytes(envelope) + b"\n"
    basis = {
        "project_id": selected["project_id"],
        "projection_generation": 7,
        "index_digest": _digest("1"),
        "entry": {
            "task_id": selected["task"]["task_id"],
            "completion_cycle_id": selected["completion_cycle_id"],
            "cycle_ordinal": selected["cycle_ordinal"],
            "bundle_state": "native",
            "bundle_id": selected["bundle_id"],
            "bundle_file": f"bundles/{selected['bundle_id']}.json",
            "bundle_digest": bundle_digest,
            "file_digest": "sha256:"
            + hashlib.sha256(bundle_document).hexdigest(),
            "sealed_at": selected["sealed_at"],
        },
    }
    recipe = default_recipe(
        inference_mode="codex_optional" if optional else "offline",
        declared_model_id="fixture-model" if optional else None,
    )
    descriptor = build_descriptor(
        source_kind="native_bundle", source_basis=basis, recipe=recipe
    )
    source = ValidatedEvidenceSource("native_bundle", basis, envelope)
    return descriptor, build_analysis_packet(descriptor, source)


def legacy_job(*, optional: bool = False):
    basis = {
        "project_id": "tg_project_" + ("0" * 32),
        "projection_generation": 7,
        "index_digest": _digest("1"),
        "entry": {
            "task_id": "tg_task_1111111111111111",
            "completion_cycle_id": "tg_completion_cycle_2222222222222222",
            "cycle_ordinal": 1,
            "bundle_state": "legacy_unknown",
            "bundle_id": None,
            "bundle_file": None,
            "bundle_digest": None,
            "file_digest": None,
            "sealed_at": None,
        },
    }
    recipe = default_recipe(
        inference_mode="codex_optional" if optional else "offline",
        declared_model_id="fixture-model" if optional else None,
    )
    descriptor = build_descriptor(
        source_kind="legacy_index_entry", source_basis=basis, recipe=recipe
    )
    source = ValidatedEvidenceSource("legacy_index_entry", basis, None)
    return descriptor, build_analysis_packet(descriptor, source)


def adapter_document(
    descriptor: dict[str, object], claims: list[dict[str, object]]
) -> bytes:
    return reference_json_bytes(
        {
            "output_schema_version": 1,
            "analysis_job_id": descriptor["analysis_job_id"],
            "source_key": descriptor["source_key"],
            "recipe_digest": descriptor["recipe_digest"],
            "claims": claims,
        }
    )


class AnalysisValidatorTests(unittest.TestCase):
    def test_offline_native_exact_null_projection_and_independent_digests(self):
        descriptor, packet = native_job()
        result = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state="disabled",
        )
        payload = result.envelope["payload"]
        self.assertIsNone(payload["legacy_absence"])
        self.assertEqual(payload["llm_derived"], [])
        self.assertEqual(payload["uncertainties"], [])
        self.assertNotIn(
            "inference_unavailable", {item["code"] for item in payload["omissions"]}
        )

        element_bytes = lambda value: reference_json_bytes(value)
        for key in (
            "structural_facts",
            "trusted_caller_declarations",
            "omissions",
            "declared_code_occurrences",
            "citations",
        ):
            encoded = [element_bytes(item) for item in payload[key]]
            self.assertEqual(encoded, sorted(encoded), key)
            self.assertEqual(len(encoded), len(set(encoded)), key)

        citation_by_id = {
            item["citation_id"]: item for item in payload["citations"]
        }
        empty_entries = next(
            item
            for item in payload["structural_facts"]
            if item["fact_kind"] == "artifact_entry" and item["value"] == []
        )
        self.assertEqual(
            citation_by_id[empty_entries["citation_ids"][0]]["citation_kind"],
            "bundle",
        )
        null_provenance = next(
            item
            for item in payload["structural_facts"]
            if item["fact_kind"] == "review_provenance"
            and item["value"] is None
        )
        self.assertEqual(len(null_provenance["citation_ids"]), 2)
        null_citations = [
            citation_by_id[citation_id]
            for citation_id in null_provenance["citation_ids"]
        ]
        self.assertEqual(
            {item["citation_kind"] for item in null_citations},
            {"review_provenance", "evidence_reference"},
        )

        expected_report_digest = "sha256:" + hashlib.sha256(
            REPORT_DOMAIN + reference_json_bytes(payload)
        ).hexdigest()
        self.assertEqual(result.report_digest, expected_report_digest)
        report_id_body = (
            descriptor["source_key"].encode("ascii")
            + b"\0"
            + descriptor["recipe_digest"].encode("ascii")
            + b"\0disabled\0offline-null"
        )
        expected_report_id = "tg_analysis_report_" + hashlib.sha256(
            REPORT_ID_DOMAIN + report_id_body
        ).hexdigest()[:16]
        self.assertEqual(result.report_id, expected_report_id)
        self.assertEqual(
            result.render_digest,
            "sha256:" + hashlib.sha256(result.markdown_bytes).hexdigest(),
        )
        self.assertEqual(
            validate_report_document(
                result.report_document,
                descriptor=descriptor,
                packet=packet,
                inference_state="disabled",
            ),
            result,
        )

    def test_v1_provenance_declarations_occurrences_and_duplicate_rejection(self):
        payload = v1_payload()
        descriptor, packet = native_job(payload=payload)
        result = build_analysis_report(
            descriptor=descriptor, packet=packet, inference_state="disabled"
        )
        report = result.envelope["payload"]
        declarations = {
            (item["declaration_kind"], json.dumps(item["value"], sort_keys=True))
            for item in report["trusted_caller_declarations"]
        }
        expected = {
            ("reviewer_class", '"human"'),
            ("model_state", '"not_applicable"'),
            ("declared_model_id", "null"),
            ("skill_state", '"not_applicable"'),
            ("declared_skill_id", "null"),
            ("declared_skill_version", "null"),
            ("context_relation", '"not_applicable"'),
            ("profile", '"general"'),
            ("lens", '"correctness"'),
            ("method", '"review_packet_inspection"'),
        }
        self.assertEqual(declarations, expected)
        self.assertEqual(
            {(item["kind"], item["code"]) for item in report["declared_code_occurrences"]},
            {
                ("profile", "general"),
                ("lens", "correctness"),
                ("method", "review_packet_inspection"),
            },
        )

        duplicate = v1_payload()
        duplicate["review_receipts"][0]["review_provenance"]["review_profiles"] = [
            "general",
            "general",
        ]
        with self.assertRaises(EvidenceConsumerError) as raised:
            native_job(payload=duplicate)
        self.assertEqual(raised.exception.code, "source_invalid")

    def test_same_declared_codes_across_receipts_remain_distinct_occurrences(self):
        payload = v1_payload()
        second = deepcopy(payload["review_receipts"][0])
        second["review_receipt_id"] = "tg_review_receipt_" + "a" * 16
        second["reviewer_key"] = "second-reviewer"
        second["review_provenance"]["review_provenance_id"] = (
            "tg_review_provenance_" + "b" * 16
        )
        second["review_provenance"]["digest"] = _digest("b")
        payload["review_receipts"].append(second)
        payload["task"]["review_tier"] = 2
        payload["evidence_references"].append(
            _reference(
                payload,
                suffix="a",
                source_kind="review_receipt",
                source_state="recorded",
                source_id=second["review_receipt_id"],
                assurance_class="bound_attestation",
                producer_class="trusted_caller",
            )
        )
        review_link = deepcopy(
            next(
                item
                for item in payload["criterion_links"]
                if item["relation"] == "review_assessment"
            )
        )
        review_link.update(
            {
                "criterion_evidence_link_id": (
                    "tg_criterion_evidence_link_" + "e" * 16
                ),
                "evidence_reference_id": "tg_evidence_reference_" + "a" * 16,
            }
        )
        payload["criterion_links"].append(review_link)
        payload["criterion_links"] = sorted(
            payload["criterion_links"],
            key=lambda item: (
                {"acceptance": 0, "verification": 1}[
                    next(
                        criterion["kind"]
                        for criterion in payload["criteria"]
                        if criterion["criterion_id"] == item["criterion_id"]
                    )
                ],
                item["criterion_id"].encode("utf-8"),
                {
                    "verification_attestation": 0,
                    "review_assessment": 1,
                    "review_finding": 2,
                    "completion_basis": 3,
                }[item["relation"]],
                item["evidence_reference_id"].encode("utf-8"),
                item["criterion_evidence_link_id"].encode("utf-8"),
            ),
        )
        payload["evidence_references"] = sorted(
            payload["evidence_references"], key=_reference_order
        )
        descriptor, packet = native_job(payload=payload)
        report = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state="disabled",
        ).envelope["payload"]
        occurrences = report["declared_code_occurrences"]
        self.assertEqual(len(occurrences), 6)
        for kind, code in (
            ("profile", "general"),
            ("lens", "correctness"),
            ("method", "review_packet_inspection"),
        ):
            matching = [
                item
                for item in occurrences
                if item["kind"] == kind and item["code"] == code
            ]
            self.assertEqual(len(matching), 2)
            self.assertEqual(len({item["review_receipt_id"] for item in matching}), 2)

    def test_conditional_model_and_skill_states_are_copied_without_inference(self):
        cases = (
            ("human", "not_applicable", None, "not_applicable", None, None, "not_applicable"),
            (
                "deterministic_tool",
                "not_applicable",
                None,
                "not_applicable",
                None,
                None,
                "not_applicable",
            ),
            ("llm", "declared", "model-a", "not_used", None, None, "fresh_context"),
            (
                "llm",
                "declared",
                "model-a",
                "declared",
                "skill-a",
                "1.0",
                "forked_context",
            ),
            ("hybrid", "unknown", None, "unknown", None, None, "external_context"),
            ("unknown", "unknown", None, "unknown", None, None, "unknown"),
        )
        for (
            reviewer_class,
            model_state,
            model_id,
            skill_state,
            skill_id,
            skill_version,
            context_relation,
        ) in cases:
            with self.subTest(reviewer_class=reviewer_class, skill_state=skill_state):
                payload = v1_payload()
                provenance = payload["review_receipts"][0]["review_provenance"]
                provenance.update(
                    {
                        "reviewer_class": reviewer_class,
                        "model_state": model_state,
                        "declared_model_id": model_id,
                        "skill_state": skill_state,
                        "declared_skill_id": skill_id,
                        "declared_skill_version": skill_version,
                        "context_relation": context_relation,
                    }
                )
                descriptor, packet = native_job(payload=payload)
                report = build_analysis_report(
                    descriptor=descriptor,
                    packet=packet,
                    inference_state="disabled",
                ).envelope["payload"]
                declarations = {
                    item["declaration_kind"]: item["value"]
                    for item in report["trusted_caller_declarations"]
                    if item["declaration_kind"]
                    not in {"profile", "lens", "method"}
                }
                self.assertEqual(
                    declarations,
                    {
                        "reviewer_class": reviewer_class,
                        "model_state": model_state,
                        "declared_model_id": model_id,
                        "skill_state": skill_state,
                        "declared_skill_id": skill_id,
                        "declared_skill_version": skill_version,
                        "context_relation": context_relation,
                    },
                )

    def test_empty_provenance_code_collections_use_bundle_citations(self):
        payload = v1_payload()
        provenance = payload["review_receipts"][0]["review_provenance"]
        provenance["review_profiles"] = []
        provenance["review_lenses"] = []
        provenance["method_codes"] = []
        descriptor, packet = native_job(payload=payload)
        report = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state="disabled",
        ).envelope["payload"]
        empty_facts = [
            item
            for item in report["structural_facts"]
            if item["fact_kind"] == "review_provenance" and item["value"] == []
        ]
        self.assertEqual(len(empty_facts), 3)
        citations = {item["citation_id"]: item for item in report["citations"]}
        for fact in empty_facts:
            self.assertEqual(len(fact["citation_ids"]), 1)
            citation = citations[fact["citation_ids"][0]]
            self.assertEqual(citation["citation_kind"], "bundle")
            self.assertIsNone(citation["entity_id"])
            self.assertIsNone(citation["entity_digest"])

    def test_reference_cardinality_fails_closed(self):
        payload = native_payload()
        payload["evidence_references"] = [
            row
            for row in payload["evidence_references"]
            if row["source_kind"] != "completion_evidence"
        ]
        with self.assertRaises(EvidenceConsumerError) as raised:
            native_job(payload=payload)
        self.assertEqual(raised.exception.code, "source_invalid")

    def test_legacy_report_is_index_only(self):
        descriptor, packet = legacy_job()
        result = build_analysis_report(
            descriptor=descriptor, packet=packet, inference_state="disabled"
        )
        payload = result.envelope["payload"]
        self.assertEqual(payload["structural_facts"], [])
        self.assertEqual(payload["trusted_caller_declarations"], [])
        self.assertEqual(payload["declared_code_occurrences"], [])
        self.assertEqual(len(payload["citations"]), 1)
        citation = payload["citations"][0]
        self.assertEqual(citation["citation_kind"], "legacy_index_entry")
        self.assertEqual(
            set(citation),
            {
                "citation_id",
                "citation_kind",
                "source_key",
                "project_id",
                "projection_generation",
                "index_digest",
                "task_id",
                "completion_cycle_id",
                "cycle_ordinal",
            },
        )
        self.assertEqual(
            payload["legacy_absence"],
            {
                "state": "legacy_unknown",
                "receipt_detail": "unavailable",
                "provenance_detail": "unavailable",
                "citation_id": citation["citation_id"],
            },
        )
        self.assertEqual(
            payload["omissions"],
            [{"code": "legacy_detail_unavailable", "citation_ids": [citation["citation_id"]]}],
        )
        self.assertEqual(
            payload["uncertainties"],
            [{"code": "legacy_absence", "citation_ids": [citation["citation_id"]]}],
        )

    def test_adapter_output_and_succeeded_report_are_exactly_bound(self):
        descriptor, packet = native_job(optional=True)
        claims = [
            {
                "text": "The task title is cited.",
                "source_refs": [
                    {
                        "kind": "native_pointer",
                        "json_pointer": "/payload/task/title",
                    }
                ],
                "uncertainty": "none",
            }
        ]
        document = adapter_document(descriptor, claims)
        output = validate_adapter_output(
            document, descriptor=descriptor, packet=packet
        )
        self.assertEqual(
            output.accepted_output_digest,
            "sha256:" + hashlib.sha256(OUTPUT_DOMAIN + document).hexdigest(),
        )
        prompt_digest = FIXED_PROMPT_DIGEST
        report = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state="succeeded",
            adapter_output=output,
            prompt_digest=prompt_digest,
        )
        derived = report.envelope["payload"]["llm_derived"]
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0]["tag"], "llm_derived/batch_analyzer/1")
        self.assertIs(derived[0]["non_authoritative"], True)
        self.assertEqual(
            report.envelope["payload"]["reproducibility"]["accepted_output_digest"],
            output.accepted_output_digest,
        )

        recovered = validate_recovery_report_document(
            report.report_document,
            descriptor=descriptor,
            packet=packet,
            inference_state="succeeded",
            accepted_output_digest=output.accepted_output_digest,
            expected_prompt_digest=prompt_digest,
            expected_report_id=report.report_id,
            expected_report_digest=report.report_digest,
            expected_render_digest=report.render_digest,
        )
        self.assertEqual(recovered, report)

        with self.assertRaises(AnalysisValidationError) as raised:
            validate_recovery_report_document(
                report.report_document,
                descriptor=descriptor,
                packet=packet,
                inference_state="succeeded",
                accepted_output_digest=output.accepted_output_digest,
                expected_prompt_digest=_digest("3"),
                expected_report_id=report.report_id,
                expected_report_digest=report.report_digest,
                expected_render_digest=report.render_digest,
            )
        self.assertEqual(raised.exception.code, "report_invalid")

    def test_recovery_rejects_forged_intent_digest_and_derived_citation(self):
        descriptor, packet = native_job(optional=True)
        claims = [
            {
                "text": "The task title is cited.",
                "source_refs": [
                    {"kind": "native_pointer", "json_pointer": "/payload/task/title"}
                ],
                "uncertainty": "none",
            }
        ]
        output = validate_adapter_output(
            adapter_document(descriptor, claims),
            descriptor=descriptor,
            packet=packet,
        )
        report = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state="succeeded",
            adapter_output=output,
            prompt_digest=FIXED_PROMPT_DIGEST,
        )
        with self.assertRaises(AnalysisValidationError) as raised:
            validate_recovery_report_document(
                report.report_document,
                descriptor=descriptor,
                packet=packet,
                inference_state="succeeded",
                accepted_output_digest=output.accepted_output_digest,
                expected_prompt_digest=FIXED_PROMPT_DIGEST,
                expected_report_id=report.report_id,
                expected_report_digest=_digest("0"),
                expected_render_digest=report.render_digest,
            )
        self.assertEqual(raised.exception.code, "report_invalid")

        forged = deepcopy(report.envelope)
        forged["payload"]["llm_derived"][0]["citation_ids"] = [
            "tg_analysis_citation_ffffffffffffffff"
        ]
        forged["report_digest"] = "sha256:" + hashlib.sha256(
            REPORT_DOMAIN + reference_json_bytes(forged["payload"])
        ).hexdigest()
        forged_document = reference_json_bytes(forged) + b"\n"
        with self.assertRaises(AnalysisValidationError) as raised:
            validate_recovery_report_document(
                forged_document,
                descriptor=descriptor,
                packet=packet,
                inference_state="succeeded",
                accepted_output_digest=output.accepted_output_digest,
                expected_prompt_digest=FIXED_PROMPT_DIGEST,
                expected_report_id=report.report_id,
                expected_report_digest=forged["report_digest"],
                expected_render_digest=report.render_digest,
            )
        self.assertEqual(raised.exception.code, "report_invalid")

    def test_optional_report_rejects_self_consistent_nonfixed_prompt_digest(self):
        descriptor, packet = legacy_job(optional=True)
        with self.assertRaises(AnalysisValidationError) as raised:
            build_analysis_report(
                descriptor=descriptor,
                packet=packet,
                inference_state="policy_blocked",
                prompt_digest=_digest("0"),
            )
        self.assertEqual(raised.exception.code, "report_invalid")

    def test_unsupported_recipe_versions_fail_closed_on_every_validator_path(self):
        descriptor, packet = native_job(optional=True)
        prompt_digest = FIXED_PROMPT_DIGEST
        report = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state="policy_blocked",
            prompt_digest=prompt_digest,
        )

        for field in (
            "producer_version",
            "report_schema_version",
            "renderer_version",
            "prompt_schema_version",
        ):
            with self.subTest(field=field):
                recipe = deepcopy(descriptor["recipe"])
                recipe[field] = 2
                future_descriptor = build_descriptor(
                    source_kind="native_bundle",
                    source_basis=descriptor["source_basis"],
                    recipe=recipe,
                )
                source = ValidatedEvidenceSource(
                    "native_bundle",
                    future_descriptor["source_basis"],
                    packet.value["source"],
                )
                future_packet = build_analysis_packet(future_descriptor, source)

                with self.assertRaises(AnalysisValidationError) as raised:
                    validate_adapter_output(
                        adapter_document(future_descriptor, []),
                        descriptor=future_descriptor,
                        packet=future_packet,
                    )
                self.assertEqual(raised.exception.code, "invalid_output")

                with self.assertRaises(AnalysisValidationError) as raised:
                    build_analysis_report(
                        descriptor=future_descriptor,
                        packet=future_packet,
                        inference_state="policy_blocked",
                        prompt_digest=prompt_digest,
                    )
                self.assertEqual(raised.exception.code, "report_invalid")

                with self.assertRaises(AnalysisValidationError) as raised:
                    validate_recovery_report_document(
                        report.report_document,
                        descriptor=future_descriptor,
                        packet=future_packet,
                        inference_state="policy_blocked",
                        accepted_output_digest=None,
                        expected_prompt_digest=prompt_digest,
                        expected_report_id=report.report_id,
                        expected_report_digest=report.report_digest,
                        expected_render_digest=report.render_digest,
                    )
                self.assertEqual(raised.exception.code, "report_invalid")

    def test_legacy_adapter_can_only_cite_the_single_index_basis(self):
        descriptor, packet = legacy_job(optional=True)
        claims = [
            {
                "text": "Receipt detail is unavailable for the legacy cycle.",
                "source_refs": [{"kind": "legacy_basis", "json_pointer": None}],
                "uncertainty": "legacy_absence",
            }
        ]
        output = validate_adapter_output(
            adapter_document(descriptor, claims),
            descriptor=descriptor,
            packet=packet,
        )
        report = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state="succeeded",
            adapter_output=output,
            prompt_digest=FIXED_PROMPT_DIGEST,
        ).envelope["payload"]
        self.assertEqual(report["structural_facts"], [])
        self.assertEqual(len(report["citations"]), 1)
        citation_id = report["citations"][0]["citation_id"]
        self.assertEqual(report["llm_derived"][0]["citation_ids"], [citation_id])
        self.assertEqual(
            report["uncertainties"],
            [{"code": "legacy_absence", "citation_ids": [citation_id]}],
        )
        self.assertEqual(
            {item["code"] for item in report["omissions"]},
            {"legacy_detail_unavailable"},
        )

    def test_adapter_negative_cases_do_not_normalize_or_retain(self):
        descriptor, packet = native_job(optional=True)
        valid_claim = {
            "text": "Bound statement.",
            "source_refs": [
                {"kind": "native_pointer", "json_pointer": "/payload/task/title"}
            ],
            "uncertainty": "none",
        }
        invalid_claims = (
            [
                {
                    **valid_claim,
                    "source_refs": [
                        {"kind": "native_pointer", "json_pointer": "/payload/missing"}
                    ],
                }
            ],
            [{**valid_claim, "uncertainty": "legacy_absence"}],
            [
                {
                    **valid_claim,
                    "source_refs": valid_claim["source_refs"] * 2,
                }
            ],
            [{**valid_claim, "text": "Authorization: Bearer secret-value"}],
            [{**valid_claim, "text": "dispatch_authorization=1"}],
            [{**valid_claim, "text": "Basic dXNlcjpwYXNz"}],
        )
        for claims in invalid_claims:
            with self.subTest(claims=claims):
                with self.assertRaises(AnalysisValidationError) as raised:
                    validate_adapter_output(
                        adapter_document(descriptor, claims),
                        descriptor=descriptor,
                        packet=packet,
                    )
                self.assertEqual(raised.exception.code, "invalid_output")

        noncanonical = b'{"output_schema_version":1, "analysis_job_id":"x"}'
        with self.assertRaises(AnalysisValidationError) as raised:
            validate_adapter_output(
                noncanonical, descriptor=descriptor, packet=packet
            )
        self.assertEqual(raised.exception.code, "invalid_output")
        with self.assertRaises(AnalysisValidationError) as raised:
            validate_adapter_output(
                b"x" * (ADAPTER_OUTPUT_MAX_BYTES + 1),
                descriptor=descriptor,
                packet=packet,
            )
        self.assertEqual(raised.exception.code, "output_too_large")
        with self.assertRaises(AnalysisValidationError) as raised:
            validate_adapter_output(
                None,
                descriptor=descriptor,
                packet=packet,
            )
        self.assertEqual(raised.exception.code, "invalid_output")

    def test_validator_import_graph_excludes_task_and_storage_modules(self):
        script = f"""
import importlib.abc
import sys

blocked = {{
    'task_governance_tool.tasks',
    'task_governance_tool.storage',
    'task_governance_tool.evidence_projection',
}}

class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            raise RuntimeError(fullname)
        return None

sys.meta_path.insert(0, Reject())
sys.path.insert(0, {str(SCRIPTS_ROOT)!r})
import task_governance_tool.analysis_validator
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_same_citation_id_with_different_bytes_fails_closed(self):
        descriptor, packet = native_job()
        with patch(
            "task_governance_tool.analysis_validator._citation_id",
            return_value="tg_analysis_citation_0000000000000000",
        ):
            with self.assertRaises(AnalysisValidationError) as raised:
                build_analysis_report(
                    descriptor=descriptor,
                    packet=packet,
                    inference_state="disabled",
                )
        self.assertEqual(raised.exception.code, "report_invalid")

    def test_report_rederivation_rejects_self_consistent_substitution(self):
        descriptor, packet = legacy_job()
        result = build_analysis_report(
            descriptor=descriptor, packet=packet, inference_state="disabled"
        )
        changed = deepcopy(result.envelope)
        changed["payload"]["legacy_absence"]["receipt_detail"] = "invented"
        changed["report_digest"] = "sha256:" + hashlib.sha256(
            REPORT_DOMAIN + reference_json_bytes(changed["payload"])
        ).hexdigest()
        document = reference_json_bytes(changed) + b"\n"
        with self.assertRaises(AnalysisValidationError) as raised:
            validate_report_document(
                document,
                descriptor=descriptor,
                packet=packet,
                inference_state="disabled",
            )
        self.assertEqual(raised.exception.code, "report_invalid")

    def test_report_capacity_keeps_a_prefix_and_records_omission(self):
        descriptor, packet = native_job(optional=True)
        claims = [
            {
                "text": character * 800,
                "source_refs": [
                    {"kind": "native_pointer", "json_pointer": "/payload/task/title"}
                ],
                "uncertainty": "none",
            }
            for character in ("a", "b")
        ]
        claims.sort(key=reference_json_bytes)
        output = validate_adapter_output(
            adapter_document(descriptor, claims),
            descriptor=descriptor,
            packet=packet,
        )
        full = build_analysis_report(
            descriptor=descriptor,
            packet=packet,
            inference_state="succeeded",
            adapter_output=output,
            prompt_digest=FIXED_PROMPT_DIGEST,
        )
        with patch(
            "task_governance_tool.analysis_validator.REPORT_JSON_MAX_BYTES",
            len(full.report_document) - 500,
        ):
            truncated = build_analysis_report(
                descriptor=descriptor,
                packet=packet,
                inference_state="succeeded",
                adapter_output=output,
                prompt_digest=FIXED_PROMPT_DIGEST,
            )
        self.assertLess(
            len(truncated.envelope["payload"]["llm_derived"]), len(claims)
        )
        self.assertIn(
            "claim_capacity_exceeded",
            {item["code"] for item in truncated.envelope["payload"]["omissions"]},
        )


if __name__ == "__main__":
    unittest.main()
