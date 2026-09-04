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

from tests.evidence_reader_oracle import (  # noqa: E402
    ValidatedEvidenceSource,
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.evidence_projection import (  # noqa: E402
    BUNDLE_V2_DOMAIN,
    INDEX_V2_DOMAIN,
    build_bundle_artifact,
    build_index_artifact,
)
from tests.evidence_test_support import (  # noqa: E402
    reference_json_bytes,
    refresh_bundle_seals,
    valid_native_payload,
)


class M242EvidenceCompatibilityTests(unittest.TestCase):
    def test_schema20_not_required_v2_has_exact_null_runner_contract(self) -> None:
        payload = deepcopy(valid_native_payload())
        v1_keys = set(payload)
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
        expected_digest = "sha256:" + hashlib.sha256(
            BUNDLE_V2_DOMAIN + reference_json_bytes(bundle.payload)
        ).hexdigest()

        self.assertEqual(bundle.bundle_digest, expected_digest)
        self.assertEqual(bundle.envelope["format_version"], 2)
        self.assertEqual(
            set(bundle.payload),
            v1_keys | {"verification_basis", "runner_observation"},
        )
        self.assertEqual(
            bundle.payload["verification_basis"],
            {
                "basis_version": 1,
                "kind": "not_required",
                "runner_observation_id": None,
                "verification_receipt_id": None,
            },
        )
        self.assertIsNone(bundle.payload["runner_observation"])

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
                "index_format_version": 2,
                "source_schema_version": 20,
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
        self.assertEqual(source.source, bundle.envelope)
        self.assertEqual(source.source_basis["index_format_version"], 2)
        self.assertEqual(source.source_basis["source_schema_version"], 20)
        self.assertEqual(source.source_basis["entry"], entry)

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
            self.assertEqual(source.source, bundle.envelope)
            self.assertEqual(source.source_basis["index_format_version"], 2)
            self.assertEqual(source.source_basis["source_schema_version"], 20)
            self.assertEqual(source.source_basis["entry"], native)
            legacy_source = validate_evidence_source(validated_index, legacy)
            self.assertIsNone(legacy_source.source)
            self.assertEqual(legacy_source.source_basis["entry"], legacy)


if __name__ == "__main__":
    unittest.main()
