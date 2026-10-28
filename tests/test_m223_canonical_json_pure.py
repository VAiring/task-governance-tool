from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))
try:
    from task_governance_tool.evidence_projection import (
        EvidenceProjectionError,
        assemble_bundle_payload,
        assemble_index_payload,
        canonical_json_bytes,
        canonical_json_document_bytes,
    )
finally:
    sys.path.pop(0)


class CanonicalJsonPureTests(unittest.TestCase):
    def test_exact_sorted_utf8_and_control_escaping(self) -> None:
        value = {
            "\U00010000": "non-bmp",
            "\ue000": "private",
            "z": '"/\\\b\f\n\r\t\x00\x1f',
            "a": [None, False, True, -12, 0, "é/"],
        }
        expected = (
            b'{"a":[null,false,true,-12,0,"\xc3\xa9/"],'
            b'"z":"\\"/\\\\\\b\\f\\n\\r\\t\\u0000\\u001f",'
            b'"\xee\x80\x80":"private","\xf0\x90\x80\x80":"non-bmp"}'
        )
        self.assertEqual(canonical_json_bytes(value), expected)
        self.assertEqual(
            canonical_json_document_bytes(value),
            expected + b"\n",
        )

    def test_no_unicode_normalization(self) -> None:
        composed = canonical_json_bytes({"v": "é"})
        decomposed = canonical_json_bytes({"v": "e\u0301"})
        self.assertNotEqual(composed, decomposed)
        self.assertEqual(composed, b'{"v":"\xc3\xa9"}')
        self.assertEqual(decomposed, b'{"v":"e\xcc\x81"}')

    def test_rejects_non_json_numeric_container_key_and_unicode_values(self) -> None:
        cyclic: list[object] = []
        cyclic.append(cyclic)
        invalid = (
            1.0,
            math.nan,
            math.inf,
            (1, 2),
            {1: "value"},
            {"v": "\ud800"},
            cyclic,
            object(),
        )
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(EvidenceProjectionError) as raised:
                    canonical_json_bytes(value)
                self.assertEqual(
                    raised.exception.code,
                    "evidence_ledger_inconsistent",
                )

    def test_integer_and_boolean_types_remain_distinct(self) -> None:
        self.assertEqual(canonical_json_bytes(True), b"true")
        self.assertEqual(canonical_json_bytes(1), b"1")
        self.assertEqual(canonical_json_bytes(-0), b"0")

    def test_exact_integer_fields_reject_boolean_aliases(self) -> None:
        from tests.test_m223_bundle_assembly_pure import sample_payload

        review = {
            "review_receipt_id": "tg_review_receipt_0123456789abcdef",
            "reviewer_key": "independent-reviewer",
            "receipt_kind": "independent",
            "verdict": "pass",
            "summary": "review passed",
            "user_approved": 0,
            "created_at": "2026-08-05T00:00:00Z",
            "review_provenance": None,
        }
        mutations = (
            (("bundle_version",), True),
            (("completion_evidence", "external_revision_approved"), False),
            (("target", "capture_version"), True),
            (("task", "review_tier"), True),
            (("verification_receipt", "verification_subject", "basis_version"), True),
        )
        for path, value in mutations:
            with self.subTest(path=path):
                payload = sample_payload()
                target = payload
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(EvidenceProjectionError):
                    assemble_bundle_payload(payload)

        for review_mutation in (
            {"user_approved": True},
            {"review_provenance": {"provenance_version": True}},
        ):
            with self.subTest(review_mutation=review_mutation):
                payload = sample_payload()
                payload["review_receipts"] = [
                    {**copy.deepcopy(review), **review_mutation}
                ]
                with self.assertRaises(EvidenceProjectionError):
                    assemble_bundle_payload(payload)

        with self.assertRaises(EvidenceProjectionError):
            assemble_index_payload(
                {
                    "source_schema_version": 19,
                    "project_id": "project-0123456789ab",
                    "projection_generation": 0,
                    "bundle_count": False,
                    "legacy_count": False,
                    "entries": [],
                }
            )


class ArtifactEncodingReuseTests(unittest.TestCase):
    def test_one_materialization_preserves_versions_and_closed_envelopes(self):
        from unittest import mock
        from task_governance_tool import evidence_projection as codec
        from tests.test_m223_bundle_assembly_pure import sample_payload, reference_json_bytes
        from tests.test_m23s_schema22_projection import _payload
        for version in (19, 20, 21, 22, 29, 30, 31, 32, 33, 34):
            bundle = sample_payload() if version == 19 else _payload("not_required", source_version=22)
            bundle["source_schema_version"] = version
            if version >= 30:
                bundle["bundle_version"] = 4
                bundle["review_continuation_basis"] = None
            index = {"source_schema_version":version, "project_id":"project-fixture",
                "projection_generation":0, "bundle_count":0, "legacy_count":0, "entries":[]}
            for basis, builder, digest_name in ((bundle, codec.build_bundle_artifact, "bundle_digest"),
                                               (index, codec.build_index_artifact, "index_digest")):
                with self.subTest(version=version, builder=builder.__name__), mock.patch.object(
                    codec, "canonical_json_bytes", wraps=codec.canonical_json_bytes) as encoded:
                    artifact = builder(basis)
                    self.assertEqual(encoded.call_count, 1)
                    self.assertEqual(artifact.payload_bytes, reference_json_bytes(artifact.payload))
                    self.assertEqual(artifact.document, reference_json_bytes(artifact.envelope)+b"\n")
                    self.assertIs(artifact.envelope["payload"], artifact.payload)
                    digest = getattr(codec, digest_name.replace("_digest", "_payload_digest"))(basis)
                    self.assertEqual(getattr(artifact, digest_name), digest)

    def test_validation_walk_rejects_same_values_without_container_encoding(self):
        from task_governance_tool import evidence_projection as codec
        cyclic = {}; cyclic["nested"] = cyclic
        for invalid in (1.5, {1:"bad"}, {"x":"\ud800"}, cyclic, {"x":(1,)}):
            for validate_only in (False, True):
                with self.subTest(value=type(invalid).__name__, validate_only=validate_only):
                    with self.assertRaises(codec.EvidenceProjectionError):
                        codec._canonical_json_bytes(invalid, _validate_only=validate_only)

    def test_large_payload_mutation_is_reencoded_and_document_limits_are_exact(self):
        from unittest import mock
        from task_governance_tool import evidence_projection as codec
        from tests.test_m223_bundle_assembly_pure import sample_payload
        for size in (8, 100000):
            payload = sample_payload()
            payload["task"]["description"] = "é/\n" * size
            first = codec.build_bundle_artifact(payload)
            payload["task"]["description"] += "changed"
            second = codec.build_bundle_artifact(payload)
            self.assertNotEqual(first.document, second.document)
            self.assertNotEqual(first.bundle_digest, second.bundle_digest)
            with mock.patch.object(codec, "BUNDLE_MAX_BYTES", len(second.document)):
                self.assertEqual(codec.build_bundle_artifact(payload).document, second.document)
            with mock.patch.object(codec, "BUNDLE_MAX_BYTES", len(second.document)-1):
                with self.assertRaises(codec.EvidenceProjectionError):
                    codec.build_bundle_artifact(payload)
        index = {"source_schema_version":34,"project_id":"é/\n","projection_generation":0,
                 "bundle_count":0,"legacy_count":0,"entries":[]}
        first = codec.build_index_artifact(index)
        with mock.patch.object(codec, "INDEX_MAX_BYTES", len(first.document)):
            self.assertEqual(codec.build_index_artifact(index).document, first.document)
        with mock.patch.object(codec, "INDEX_MAX_BYTES", len(first.document)-1):
            with self.assertRaises(codec.EvidenceProjectionError):
                codec.build_index_artifact(index)
        index["project_id"] += "changed"
        self.assertNotEqual(codec.build_index_artifact(index).document, first.document)


if __name__ == "__main__":
    unittest.main()
