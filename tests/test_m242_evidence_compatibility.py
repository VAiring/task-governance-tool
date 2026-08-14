import hashlib
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.analysis_contracts import (  # noqa: E402
    build_descriptor,
    canonical_json_bytes,
    default_recipe,
    descriptor_replay_matches,
    sealed_domain_digest,
)
from task_governance_tool.analysis_packet import build_analysis_packet  # noqa: E402
from task_governance_tool.analysis_validator import (  # noqa: E402
    AnalysisValidationError,
    build_analysis_report,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    EvidenceConsumerError,
    ValidatedEvidenceSource,
    read_evidence_index,
    revalidate_descriptor_source,
    validate_evidence_source,
)
from task_governance_tool.evidence_projection import (  # noqa: E402
    BUNDLE_V2_DOMAIN,
    INDEX_V2_DOMAIN,
    EvidenceProjectionError,
    build_bundle_artifact,
    build_index_artifact,
)
from task_governance_tool.verification_runner import (  # noqa: E402
    verification_runner_observation_digest,
    verification_runner_policy_digest,
)
from tests.m23_test_support import (  # noqa: E402
    EVIDENCE_REFERENCE_DOMAIN,
    domain_digest,
    reference_json_bytes,
    refresh_bundle_seals,
    valid_native_payload,
)


_RUNNER_OBSERVATION_DIGEST_KEYS = frozenset(
    {
        "attempt_id",
        "completed_step_count",
        "complete_plan",
        "cpu_time_ms",
        "duration_ms",
        "failed_step_ordinal",
        "finished_at",
        "gate_eligibility_version",
        "launch_state",
        "outcome",
        "peak_job_memory_bytes",
        "project_id",
        "reason",
        "resolution_id",
        "runner_implementation_digest",
        "started_at",
        "target_generation",
        "task_id",
        "route",
        "total_process_count",
        "total_step_count",
    }
)
_RUNNER_OBSERVATION_REFERENCE_KEYS = frozenset(
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
        "sandbox_provider",
        "sandbox_policy_digest",
        "runtime_digest",
        "sanitized_result_digest",
    }
)


def _refresh_runner_observation_digest(payload: dict[str, object]) -> None:
    observation = payload["runner_observation"]
    observation["sanitized_result_digest"] = verification_runner_observation_digest(
        {
            key: observation[key]
            for key in _RUNNER_OBSERVATION_DIGEST_KEYS
        }
    )


def _refresh_runner_reference_digest(payload: dict[str, object]) -> None:
    observation = payload["runner_observation"]
    reference = next(
        row
        for row in payload["evidence_references"]
        if row["source_kind"] == "runner_observation"
    )
    reference["digest"] = domain_digest(
        EVIDENCE_REFERENCE_DOMAIN,
        {
            "acceptance_criterion_id": reference["acceptance_criterion_id"],
            "assurance_class": reference["assurance_class"],
            "authority_snapshot_id": reference["authority_snapshot_id"],
            "completion_cycle_id": None,
            "contract_revision": reference["contract_revision"],
            "producer_class": "verification_runner",
            "producer_version": 1,
            "project_id": payload["project_id"],
            "source_id": observation["observation_id"],
            "source_kind": "runner_observation",
            "source_projection": {
                key: observation[key]
                for key in _RUNNER_OBSERVATION_REFERENCE_KEYS
            },
            "source_state": "recorded",
            "target_base_revision": reference["target_base_revision"] or "",
            "target_generation": reference["target_generation"],
            "target_kind": reference["target_kind"],
            "target_value": reference["target_value"],
            "task_id": payload["task"]["task_id"],
            "verification_criterion_id": reference[
                "verification_criterion_id"
            ],
        },
    )


def _runner_v2_payload() -> dict[str, object]:
    payload = deepcopy(valid_native_payload())
    payload["source_schema_version"] = 20
    payload["bundle_version"] = 2
    observation_id = "tg_verification_runner_observation_" + "a" * 16
    observation = {
        "attempt_id": None,
        "complete_plan": 0,
        "completed_step_count": 0,
        "cpu_time_ms": None,
        "duration_ms": 0,
        "failed_step_ordinal": None,
        "finished_at": "2026-08-05T00:00:00Z",
        "gate_eligibility_version": 0,
        "launch_state": "no_launch",
        "observation_id": observation_id,
        "outcome": "not_run",
        "peak_job_memory_bytes": None,
        "project_id": payload["project_id"],
        "resolution_id": "tg_verification_runner_resolution_" + "b" * 16,
        "total_process_count": None,
        "plan_blob_object_id": None,
        "plan_id": None,
        "plan_raw_digest": None,
        "plan_semantic_digest": None,
        "plan_version": None,
        "reason": "plan_absent",
        "route": "m21_fallback",
        "runner_implementation_version": "taskgov-verification-runner/1",
        "runner_implementation_digest": "sha256:" + "1" * 64,
        "runner_policy_digest": verification_runner_policy_digest(),
        "runtime_digest": None,
        "sandbox_policy_digest": None,
        "sandbox_provider": None,
        "sanitized_result_digest": "sha256:" + "3" * 64,
        "started_at": "2026-08-05T00:00:00Z",
        "target_generation": payload["target"]["generation"],
        "task_id": payload["task"]["task_id"],
        "total_step_count": 0,
    }
    verification = payload["verification_receipt"]
    payload["verification_basis"] = {
        "basis_version": 1,
        "kind": "caller_attestation",
        "runner_observation_id": None,
        "verification_receipt_id": verification["verification_receipt_id"],
    }
    payload["runner_observation"] = observation
    _refresh_runner_observation_digest(payload)

    template = deepcopy(payload["evidence_references"][0])
    template.update(
        {
            "evidence_reference_id": "tg_evidence_reference_" + "0f" * 8,
            "source_kind": "runner_observation",
            "source_state": "recorded",
            "source_id": observation_id,
            "assurance_class": "machine_observed",
            "producer_class": "verification_runner",
            "producer_version": 1,
            "completion_cycle_id": None,
            "digest": "sha256:" + "0" * 64,
        }
    )
    payload["evidence_references"].append(template)
    verification_id = next(
        row["criterion_id"]
        for row in payload["criteria"]
        if row["kind"] == "verification"
    )
    payload["criterion_links"].append(
        {
            "criterion_evidence_link_id": (
                "tg_criterion_evidence_link_" + "0f" * 8
            ),
            "criterion_id": verification_id,
            "evidence_reference_id": template["evidence_reference_id"],
            "relation": "runner_observation",
            "assurance_class": "machine_observed",
            "producer_class": "verification_runner",
            "producer_version": 1,
        }
    )
    criterion_kind = {
        row["criterion_id"]: row["kind"] for row in payload["criteria"]
    }
    payload["criterion_links"].sort(
        key=lambda row: (
            {"acceptance": 0, "verification": 1}[criterion_kind[row["criterion_id"]]],
            row["criterion_id"],
            {
                "review_assessment": 0,
                "review_finding": 1,
                "completion_basis": 2,
                "verification_attestation": 3,
                "runner_observation": 4,
            }[row["relation"]],
            row["evidence_reference_id"],
            row["criterion_evidence_link_id"],
        )
    )
    payload["evidence_references"].sort(
        key=lambda row: (
            {
                "artifact_manifest": 0,
                "verification_receipt": 1,
                "review_receipt": 2,
                "review_finding": 3,
                "completion_evidence": 4,
                "runner_observation": 5,
            }[row["source_kind"]],
            row["source_id"],
            row["evidence_reference_id"],
        )
    )
    refresh_bundle_seals(payload)
    _refresh_runner_reference_digest(payload)
    return payload


def _schema20_launched_failure_payload() -> dict[str, object]:
    payload = _runner_v2_payload()
    payload["runner_observation"].update(
        {
            "attempt_id": "tg_verification_runner_attempt_" + "c" * 16,
            "complete_plan": 0,
            "completed_step_count": 1,
            "cpu_time_ms": 1,
            "duration_ms": 1_000,
            "failed_step_ordinal": 1,
            "finished_at": "2026-08-05T00:00:01Z",
            "launch_state": "launched",
            "outcome": "fail",
            "peak_job_memory_bytes": 1,
            "total_process_count": 1,
            "plan_blob_object_id": "a" * 40,
            "plan_id": "focused",
            "plan_raw_digest": "sha256:" + "4" * 64,
            "plan_semantic_digest": "sha256:" + "5" * 64,
            "plan_version": 1,
            "reason": "step_nonzero",
            "route": "runner",
            "runtime_digest": "sha256:" + "6" * 64,
            "total_step_count": 1,
        }
    )
    _refresh_runner_observation_digest(payload)
    _refresh_runner_reference_digest(payload)
    return payload


def _schema20_uncertain_cleanup_payload() -> dict[str, object]:
    payload = _schema20_launched_failure_payload()
    payload["runner_observation"].update(
        {
            "completed_step_count": 1,
            "cpu_time_ms": None,
            "duration_ms": 0,
            "failed_step_ordinal": None,
            "finished_at": "2026-08-05T00:00:00Z",
            "launch_state": "launch_uncertain",
            "outcome": "sandbox_cleanup_failed",
            "peak_job_memory_bytes": None,
            "total_process_count": None,
            "reason": "sandbox_cleanup_failed",
            "route": "blocked",
            "total_step_count": 2,
        }
    )
    _refresh_runner_observation_digest(payload)
    _refresh_runner_reference_digest(payload)
    return payload


def _resealed_runner_source(
    payload: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Seal an intentionally invalid Runner payload past outer digest checks."""

    _refresh_runner_reference_digest(payload)
    bundle_digest = "sha256:" + hashlib.sha256(
        BUNDLE_V2_DOMAIN + canonical_json_bytes(payload)
    ).hexdigest()
    envelope = {
        "bundle_digest": bundle_digest,
        "format_version": 2,
        "payload": payload,
    }
    file_digest = "sha256:" + hashlib.sha256(
        canonical_json_bytes(envelope) + b"\n"
    ).hexdigest()
    entry = {
        "task_id": payload["task"]["task_id"],
        "completion_cycle_id": payload["completion_cycle_id"],
        "cycle_ordinal": payload["cycle_ordinal"],
        "bundle_state": "native",
        "bundle_id": payload["bundle_id"],
        "bundle_file": f"bundles/{payload['bundle_id']}.json",
        "bundle_format_version": 2,
        "bundle_digest": bundle_digest,
        "file_digest": file_digest,
        "sealed_at": payload["sealed_at"],
    }
    basis = {
        "project_id": payload["project_id"],
        "projection_generation": 8,
        "index_digest": "sha256:" + "8" * 64,
        "entry": entry,
    }
    return basis, envelope


class M242EvidenceCompatibilityTests(unittest.TestCase):
    def test_schema20_not_required_v2_has_no_receipt_or_runner(self) -> None:
        payload = deepcopy(valid_native_payload())
        payload["source_schema_version"] = 20
        payload["bundle_version"] = 2
        payload["task"]["verification"] = ""
        payload["criteria"] = [
            row for row in payload["criteria"] if row["kind"] != "verification"
        ]
        payload["criterion_links"] = [
            row
            for row in payload["criterion_links"]
            if row["relation"] != "verification_attestation"
        ]
        payload["evidence_references"] = [
            row
            for row in payload["evidence_references"]
            if row["source_kind"] != "verification_receipt"
        ]
        for reference in payload["evidence_references"]:
            reference["verification_criterion_id"] = None
        payload["verification_receipt"] = None
        payload["omissions"] = ["verification_criterion_absent"]
        payload["verification_basis"] = {
            "basis_version": 1,
            "kind": "not_required",
            "runner_observation_id": None,
            "verification_receipt_id": None,
        }
        payload["runner_observation"] = None
        refresh_bundle_seals(payload)
        bundle = build_bundle_artifact(payload)
        entry = {
            "task_id": payload["task"]["task_id"],
            "completion_cycle_id": payload["completion_cycle_id"],
            "cycle_ordinal": payload["cycle_ordinal"],
            "bundle_state": "native",
            "bundle_id": payload["bundle_id"],
            "bundle_file": f"bundles/{payload['bundle_id']}.json",
            "bundle_format_version": 2,
            "bundle_digest": bundle.bundle_digest,
            "file_digest": bundle.file_digest,
            "sealed_at": payload["sealed_at"],
        }
        source = ValidatedEvidenceSource(
            "native_bundle",
            {
                "project_id": payload["project_id"],
                "projection_generation": 8,
                "index_digest": "sha256:" + "8" * 64,
                "entry": entry,
            },
            bundle.envelope,
        )
        self.assertEqual(
            source.source["payload"]["verification_basis"]["kind"],
            "not_required",
        )

    def test_v2_index_can_reference_unchanged_v1_bundle(self) -> None:
        bundle = build_bundle_artifact(valid_native_payload())
        legacy = {
            "task_id": "tg_task_0000000000000000",
            "completion_cycle_id": "tg_completion_cycle_0000000000000000",
            "cycle_ordinal": 1,
            "bundle_state": "legacy_unknown",
            "bundle_id": None,
            "bundle_file": None,
            "bundle_format_version": None,
            "bundle_digest": None,
            "file_digest": None,
            "sealed_at": None,
        }
        native = {
            "task_id": bundle.payload["task"]["task_id"],
            "completion_cycle_id": bundle.payload["completion_cycle_id"],
            "cycle_ordinal": bundle.payload["cycle_ordinal"],
            "bundle_state": "native",
            "bundle_id": bundle.payload["bundle_id"],
            "bundle_file": f"bundles/{bundle.payload['bundle_id']}.json",
            "bundle_format_version": 1,
            "bundle_digest": bundle.bundle_digest,
            "file_digest": bundle.file_digest,
            "sealed_at": bundle.payload["sealed_at"],
        }
        entries = sorted(
            [native, legacy],
            key=lambda row: (
                row["task_id"].encode("utf-8"),
                row["cycle_ordinal"],
                row["completion_cycle_id"].encode("utf-8"),
            ),
        )
        index = build_index_artifact(
            {
                "source_schema_version": 20,
                "project_id": bundle.payload["project_id"],
                "projection_generation": 8,
                "bundle_count": 1,
                "legacy_count": 1,
                "entries": entries,
            }
        )
        expected = "sha256:" + hashlib.sha256(
            INDEX_V2_DOMAIN + reference_json_bytes(index.payload)
        ).hexdigest()
        self.assertEqual(index.index_digest, expected)
        self.assertEqual(index.envelope["format_version"], 2)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "evidence"
            bundles = root / "bundles"
            bundles.mkdir(parents=True)
            (bundles / f"{bundle.payload['bundle_id']}.json").write_bytes(
                bundle.document
            )
            (root / "index.json").write_bytes(index.document)
            validated_index = read_evidence_index(root)
            self.assertEqual(validated_index.format_version, 2)
            self.assertEqual(validated_index.source_schema_version, 20)
            source = validate_evidence_source(validated_index, native)
            self.assertEqual(source.source["format_version"], 1)
            self.assertEqual(source.source["bundle_digest"], bundle.bundle_digest)
            old_entry = dict(native)
            old_entry.pop("bundle_format_version")
            old_descriptor = build_descriptor(
                source_kind="native_bundle",
                source_basis={
                    "project_id": bundle.payload["project_id"],
                    "projection_generation": 7,
                    "index_digest": "sha256:" + "7" * 64,
                    "entry": old_entry,
                },
                recipe=default_recipe(),
            )
            current_descriptor = build_descriptor(
                source_kind="native_bundle",
                source_basis=source.source_basis,
                recipe=default_recipe(),
            )
            self.assertTrue(
                descriptor_replay_matches(old_descriptor, current_descriptor)
            )
            replay = revalidate_descriptor_source(
                validated_index,
                old_descriptor,
            )
            self.assertEqual(replay.source_basis, old_descriptor["source_basis"])

    def test_v2_runner_is_machine_observed_in_analyzer_without_new_authority(self) -> None:
        payload = _runner_v2_payload()
        bundle = build_bundle_artifact(payload)
        expected = "sha256:" + hashlib.sha256(
            BUNDLE_V2_DOMAIN + reference_json_bytes(bundle.payload)
        ).hexdigest()
        self.assertEqual(bundle.bundle_digest, expected)
        entry = {
            "task_id": payload["task"]["task_id"],
            "completion_cycle_id": payload["completion_cycle_id"],
            "cycle_ordinal": payload["cycle_ordinal"],
            "bundle_state": "native",
            "bundle_id": payload["bundle_id"],
            "bundle_file": f"bundles/{payload['bundle_id']}.json",
            "bundle_format_version": 2,
            "bundle_digest": bundle.bundle_digest,
            "file_digest": bundle.file_digest,
            "sealed_at": payload["sealed_at"],
        }
        basis = {
            "project_id": payload["project_id"],
            "projection_generation": 8,
            "index_digest": "sha256:" + "b" * 64,
            "entry": entry,
        }
        source = ValidatedEvidenceSource("native_bundle", basis, bundle.envelope)
        descriptor = build_descriptor(
            source_kind="native_bundle",
            source_basis=source.source_basis,
            recipe=default_recipe(),
        )
        report = build_analysis_report(
            descriptor=descriptor,
            packet=build_analysis_packet(descriptor, source),
            inference_state="disabled",
        ).envelope["payload"]
        runner_facts = [
            fact
            for fact in report["structural_facts"]
            if fact["fact_kind"] == "runner_observation"
        ]
        self.assertTrue(runner_facts)
        reference = next(
            fact["value"]
            for fact in report["structural_facts"]
            if fact["fact_kind"] == "evidence_reference"
            and type(fact["value"]) is dict
            and fact["value"]["source_kind"] == "runner_observation"
        )
        self.assertEqual(
            (
                reference["assurance_class"],
                reference["producer_class"],
                reference["producer_version"],
            ),
            ("machine_observed", "verification_runner", 1),
        )
        self.assertEqual(report["llm_derived"], [])
        self.assertNotIn("gate_authority", report)

    def test_schema20_rejects_runner_selected_or_eligibility_two(self) -> None:
        payload = _runner_v2_payload()
        payload["runner_observation"]["gate_eligibility_version"] = 2
        with self.assertRaises(EvidenceProjectionError):
            build_bundle_artifact(payload)

        payload = _runner_v2_payload()
        payload["verification_basis"].update(
            {
                "kind": "runner_observation",
                "runner_observation_id": payload["runner_observation"][
                    "observation_id"
                ],
                "verification_receipt_id": None,
            }
        )
        payload["verification_receipt"] = None
        with self.assertRaises(EvidenceProjectionError):
            build_bundle_artifact(payload)

    def test_projection_and_consumer_recompute_runner_observation_digest(
        self,
    ) -> None:
        payload = _runner_v2_payload()
        payload["runner_observation"]["sanitized_result_digest"] = (
            "sha256:" + "4" * 64
        )
        with self.assertRaises(EvidenceProjectionError):
            build_bundle_artifact(payload)

        payload = _runner_v2_payload()
        payload["runner_observation"]["sanitized_result_digest"] = (
            "sha256:" + "4" * 64
        )
        basis, envelope = _resealed_runner_source(payload)
        with self.assertRaises(EvidenceConsumerError):
            ValidatedEvidenceSource("native_bundle", basis, envelope)

    def test_projection_and_consumer_bind_runner_observation_generation(
        self,
    ) -> None:
        payload = _runner_v2_payload()
        payload["runner_observation"]["target_generation"] += 1
        _refresh_runner_observation_digest(payload)
        _refresh_runner_reference_digest(payload)
        with self.assertRaises(EvidenceProjectionError):
            build_bundle_artifact(payload)

        basis, envelope = _resealed_runner_source(payload)
        with self.assertRaises(EvidenceConsumerError):
            ValidatedEvidenceSource("native_bundle", basis, envelope)

    def test_analyzer_rejects_inner_runner_tamper_after_outer_reseal(self) -> None:
        valid_payload = _runner_v2_payload()
        valid_basis, valid_envelope = _resealed_runner_source(valid_payload)
        valid_source = ValidatedEvidenceSource(
            "native_bundle",
            valid_basis,
            valid_envelope,
        )
        valid_descriptor = build_descriptor(
            source_kind="native_bundle",
            source_basis=valid_basis,
            recipe=default_recipe(),
        )
        packet = build_analysis_packet(valid_descriptor, valid_source)

        tampered_payload = deepcopy(valid_payload)
        tampered_payload["runner_observation"]["sanitized_result_digest"] = (
            "sha256:" + "4" * 64
        )
        tampered_basis, tampered_envelope = _resealed_runner_source(
            tampered_payload
        )
        tampered_descriptor = build_descriptor(
            source_kind="native_bundle",
            source_basis=tampered_basis,
            recipe=default_recipe(),
        )
        packet_value = packet.value
        packet_value["analysis_job_id"] = tampered_descriptor["analysis_job_id"]
        packet_value["source_basis"] = tampered_basis
        packet_value["source"] = tampered_envelope
        packet_bytes = canonical_json_bytes(packet_value)
        object.__setattr__(packet, "_packet_bytes", packet_bytes)
        object.__setattr__(
            packet,
            "_packet_digest",
            sealed_domain_digest("taskgov-analysis-packet-v1", packet_bytes),
        )
        with self.assertRaises(AnalysisValidationError):
            build_analysis_report(
                descriptor=tampered_descriptor,
                packet=packet,
                inference_state="disabled",
            )

    def test_projection_rejects_tampered_runner_identity_and_terminal_matrix(
        self,
    ) -> None:
        cases = (
            (
                "runner_policy",
                {"runner_policy_digest": "sha256:" + "9" * 64},
            ),
            ("wrong_reason", {"reason": "timeout"}),
            ("missing_ordinal", {"failed_step_ordinal": None}),
            (
                "first_step_create_claimed_launched",
                {"outcome": "process_error", "reason": "process_create_failed"},
            ),
        )
        for label, changes in cases:
            with self.subTest(label=label):
                payload = _schema20_launched_failure_payload()
                payload["runner_observation"].update(changes)
                with self.assertRaises(EvidenceProjectionError):
                    build_bundle_artifact(payload)

    def test_consumer_rejects_resealed_runner_identity_and_terminal_tampering(
        self,
    ) -> None:
        cases = (
            (
                "runner_policy",
                {"runner_policy_digest": "sha256:" + "9" * 64},
            ),
            ("wrong_reason", {"reason": "timeout"}),
            ("missing_ordinal", {"failed_step_ordinal": None}),
            (
                "first_step_create_claimed_launched",
                {"outcome": "process_error", "reason": "process_create_failed"},
            ),
        )
        for label, changes in cases:
            with self.subTest(label=label):
                payload = _schema20_launched_failure_payload()
                payload["runner_observation"].update(changes)
                basis, envelope = _resealed_runner_source(payload)
                with self.assertRaises(EvidenceConsumerError):
                    ValidatedEvidenceSource("native_bundle", basis, envelope)

    def test_uncertain_cleanup_preserves_known_leading_completed_steps(self) -> None:
        payload = _schema20_uncertain_cleanup_payload()
        bundle = build_bundle_artifact(payload)
        basis, _ = _resealed_runner_source(payload)
        source = ValidatedEvidenceSource(
            "native_bundle",
            basis,
            bundle.envelope,
        )
        observation = source.source["payload"]["runner_observation"]
        self.assertEqual(
            (
                observation["launch_state"],
                observation["outcome"],
                observation["completed_step_count"],
                observation["failed_step_ordinal"],
            ),
            ("launch_uncertain", "sandbox_cleanup_failed", 1, None),
        )

    def test_later_step_process_create_failure_identifies_next_ordinal(self) -> None:
        payload = _schema20_launched_failure_payload()
        payload["runner_observation"].update(
            {
                "completed_step_count": 2,
                "failed_step_ordinal": 2,
                "outcome": "process_error",
                "reason": "process_create_failed",
                "total_step_count": 2,
            }
        )
        _refresh_runner_observation_digest(payload)
        _refresh_runner_reference_digest(payload)
        bundle = build_bundle_artifact(payload)
        basis, _ = _resealed_runner_source(payload)
        source = ValidatedEvidenceSource(
            "native_bundle",
            basis,
            bundle.envelope,
        )
        observation = source.source["payload"]["runner_observation"]
        self.assertEqual(
            (
                observation["outcome"],
                observation["reason"],
                observation["failed_step_ordinal"],
            ),
            ("process_error", "process_create_failed", 2),
        )


if __name__ == "__main__":
    unittest.main()
