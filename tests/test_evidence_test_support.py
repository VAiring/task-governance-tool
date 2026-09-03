from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.evidence_test_support import (
    reference_json_bytes,
    refresh_bundle_seals,
    sealed_bundle,
    v1_native_payload,
    write_evidence_tree,
    write_mixed_evidence_tree,
)


# These vectors were captured from the original M23 fixtures before extraction.
# They compare actual output bytes, not two imports of the same implementation.
class EvidenceTestSupportTests(unittest.TestCase):
    def test_reference_json_encoding_is_literal_utf8(self):
        value = {"z": [None, True, False, -2], "日本語": "値\n", "a": {"b": 1}}
        self.assertEqual(
            reference_json_bytes(value),
            '{"a":{"b":1},"z":[null,true,false,-2],"日本語":"値\\n"}'.encode(
                "utf-8"
            ),
        )

    def test_native_bundle_matches_pre_extraction_bytes_and_digest(self):
        bundle, document = sealed_bundle()
        self.assertEqual(
            bundle["bundle_digest"],
            "sha256:98d79472e8909bbe01285f52e59b94215023d27783ad08d3491284fad397a992",
        )
        self.assertEqual(len(document), 6948)
        self.assertEqual(
            hashlib.sha256(document).hexdigest(),
            "bbbd84c23aebb65e8f5814d6701674ff1f13256f0c13da7f14612dabefebd7fb",
        )

    def test_provenance_resealing_remains_explicit(self):
        payload = v1_native_payload()
        self.assertEqual(
            hashlib.sha256(reference_json_bytes(payload)).hexdigest(),
            "587f936d790c34c86410589e09d47598d2c0bb9ebe3691ca9447f310a352c6e0",
        )
        refresh_bundle_seals(payload)
        self.assertEqual(
            hashlib.sha256(reference_json_bytes(payload)).hexdigest(),
            "09f28f44535c85ce5f0c9e4161bf6af6b1753e440c7946d35a92432aa19bdb65",
        )
        self.assertEqual(
            payload["review_receipts"][0]["review_provenance"]["digest"],
            "sha256:385a5076be27310fbd0f4b0eca5ad563543311572a6b99f31d42b61495c0ecf5",
        )

    def test_native_and_mixed_trees_match_pre_extraction_file_bytes(self):
        cases = (
            (
                write_evidence_tree,
                {
                    "bundles/tg_completion_evidence_bundle_1111111111111111.json": (
                        6948,
                        "bbbd84c23aebb65e8f5814d6701674ff1f13256f0c13da7f14612dabefebd7fb",
                    ),
                    "index.json": (
                        1015,
                        "fb90bd2b8a353250b5637d5d120bec08c74ed4bd9e4932b3be0993fbb73cdfac",
                    ),
                },
            ),
            (
                write_mixed_evidence_tree,
                {
                    "bundles/tg_completion_evidence_bundle_aaaaaaaaaaaaaaaa.json": (
                        7471,
                        "39c652ccecfc6a2b7d5aa91a6fb268a793a4b10a54c58eb69d3a495c2f44d034",
                    ),
                    "bundles/tg_completion_evidence_bundle_bbbbbbbbbbbbbbbb.json": (
                        7484,
                        "eaf753848815e590510729f693784b940deaa193250d4d513b92739f25c57f4e",
                    ),
                    "bundles/tg_completion_evidence_bundle_cccccccccccccccc.json": (
                        6948,
                        "fe2efd5ef4c8a1af3d5e22de4dca1bead7fee603282946addc0937cc0561f89b",
                    ),
                    "index.json": (
                        2242,
                        "adf04c4b48b20ebf47d01f7cf42ac72d78f553ceefbdd2cd025a4dc7eb3ac3f6",
                    ),
                },
            ),
        )
        for builder, expected in cases:
            with self.subTest(builder=builder.__name__):
                with tempfile.TemporaryDirectory() as temporary:
                    evidence = builder(Path(temporary))
                    actual = {}
                    for path in evidence.rglob("*"):
                        if path.is_file():
                            document = path.read_bytes()
                            actual[path.relative_to(evidence).as_posix()] = (
                                len(document),
                                hashlib.sha256(document).hexdigest(),
                            )
                    self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
