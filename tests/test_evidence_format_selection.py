"""Literal format/domain expectations independent of product selectors."""

from __future__ import annotations

import hashlib
import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests import evidence_reader_oracle as reader
from tests.evidence_test_support import (
    index_entries,
    reference_json_bytes,
    valid_native_payload,
)
from task_governance_tool import evidence_projection as projection


# Keep these expectations separate from the producer and the Reader's tables.
ORDINARY_FORMATS = {
    19: (1, 1),
    20: (2, 2),
    21: (2, 2),
    22: (2, 2),
    23: (2, 2),
    24: (2, 2),
    25: (2, 2),
    26: (2, 2),
    27: (2, 2),
    28: (2, 2),
    29: (2, 2),
    30: (4, 3),
    31: (4, 3),
    32: (4, 3),
}
BUNDLE_DOMAINS = {
    1: b"taskgov-completion-evidence-bundle-v1\0",
    2: b"taskgov-completion-evidence-bundle-v2\0",
    3: b"taskgov-completion-evidence-bundle-v3\0",
    4: b"taskgov-completion-evidence-bundle-v4\0",
}
INDEX_DOMAINS = {
    1: b"taskgov-evidence-index-v1\0",
    2: b"taskgov-evidence-index-v2\0",
    3: b"taskgov-evidence-index-v3\0",
}


def _digest(domain, payload):
    return "sha256:" + hashlib.sha256(domain + reference_json_bytes(payload)).hexdigest()


def _bundle_payload(source, version):
    payload = valid_native_payload()
    payload.update(source_schema_version=source, bundle_version=version)
    if version != 1:
        payload["verification_basis"] = {
            "basis_version": 1,
            "kind": "caller_attestation",
            "verification_receipt_id": payload["verification_receipt"]["verification_receipt_id"],
            "runner_observation_id": None,
        }
        payload["runner_observation"] = None
    if version in (3, 4):
        payload["review_continuation_basis"] = None
    return payload


def _bundle_envelope(payload, version, *, domain=None):
    return {
        "format_version": version,
        "bundle_digest": _digest(BUNDLE_DOMAINS[version] if domain is None else domain, payload),
        "payload": payload,
    }


def _index_payload(source, bundle, *, index_version):
    document = reference_json_bytes(bundle) + b"\n"
    entries = index_entries(document, bundle["bundle_digest"])
    if index_version != 1:
        for entry in entries:
            entry["bundle_format_version"] = (
                bundle["format_version"] if entry["bundle_state"] == "native" else None
            )
    return {
        "source_schema_version": source,
        "project_id": bundle["payload"]["project_id"],
        "projection_generation": 8,
        "bundle_count": 1,
        "legacy_count": 1,
        "entries": entries,
    }


def _index_envelope(payload, version, *, domain=None):
    return {
        "format_version": version,
        "index_digest": _digest(INDEX_DOMAINS[version] if domain is None else domain, payload),
        "payload": payload,
    }


def _source_basis(index, entry):
    return {
        "index_format_version": index["format_version"],
        "source_schema_version": index["payload"]["source_schema_version"],
        "project_id": index["payload"]["project_id"],
        "projection_generation": index["payload"]["projection_generation"],
        "index_digest": index["index_digest"],
        "entry": entry,
    }


class EvidenceFormatSelectionTests(unittest.TestCase):
    def test_every_ordinary_source_preserves_bundle_bytes_and_domains(self):
        for source, (bundle_version, index_version) in ORDINARY_FORMATS.items():
            with self.subTest(source=source):
                payload = _bundle_payload(source, bundle_version)
                expected = _bundle_envelope(payload, bundle_version)
                document = reference_json_bytes(expected) + b"\n"
                artifact = projection.build_bundle_artifact(payload)
                self.assertEqual(artifact.payload, payload)
                self.assertEqual(artifact.payload_bytes, reference_json_bytes(payload))
                self.assertEqual(artifact.envelope, expected)
                self.assertEqual(artifact.document, document)
                self.assertEqual(artifact.bundle_digest, expected["bundle_digest"])
                self.assertEqual(projection.bundle_payload_digest(payload), expected["bundle_digest"])
                self.assertEqual(artifact.file_digest, "sha256:" + hashlib.sha256(document).hexdigest())
                index = _index_envelope(
                    _index_payload(source, expected, index_version=index_version), index_version,
                )
                entry = next(row for row in index["payload"]["entries"] if row["bundle_state"] == "native")
                observed = reader.ValidatedEvidenceSource("native_bundle", _source_basis(index, entry), artifact.envelope)
                self.assertEqual(observed.source, expected)
                if source == 19:
                    # Retained original fixture vector, not regenerated by this test.
                    self.assertEqual(artifact.bundle_digest, "sha256:98d79472e8909bbe01285f52e59b94215023d27783ad08d3491284fad397a992")
                    self.assertEqual(artifact.file_digest, "sha256:bbbd84c23aebb65e8f5814d6701674ff1f13256f0c13da7f14612dabefebd7fb")

    def test_every_source_preserves_index_bytes_domains_and_legacy_absence(self):
        for source, (bundle_version, index_version) in ORDINARY_FORMATS.items():
            with self.subTest(source=source):
                bundle = _bundle_envelope(_bundle_payload(source, bundle_version), bundle_version)
                payload = _index_payload(source, bundle, index_version=index_version)
                expected = _index_envelope(payload, index_version)
                artifact = projection.build_index_artifact(payload)
                self.assertEqual(artifact.payload_bytes, reference_json_bytes(payload))
                self.assertEqual(artifact.envelope, expected)
                self.assertEqual(artifact.document, reference_json_bytes(expected) + b"\n")
                self.assertEqual(projection.index_payload_digest(payload), expected["index_digest"])
                self.assertEqual(reader._validate_index_envelope(artifact.envelope), expected)
                for entry in payload["entries"]:
                    kind = "native_bundle" if entry["bundle_state"] == "native" else "legacy_index_entry"
                    expected_source = bundle if kind == "native_bundle" else None
                    observed = reader.ValidatedEvidenceSource(kind, _source_basis(expected, entry), expected_source)
                    self.assertEqual(observed.source, expected_source)
                    self.assertEqual(observed.source_basis["entry"], entry)

    def test_later_indexes_preserve_historical_bundle_bytes_without_resealing(self):
        for bundle_source in (19, 20, 29, 30):
            bundle_version = ORDINARY_FORMATS[bundle_source][0]
            artifact = projection.build_bundle_artifact(_bundle_payload(bundle_source, bundle_version))
            original = (artifact.document, artifact.bundle_digest, artifact.file_digest)
            for index_source, (_, index_version) in ORDINARY_FORMATS.items():
                if index_source <= bundle_source:
                    continue
                with self.subTest(bundle_source=bundle_source, index_source=index_source):
                    payload = _index_payload(index_source, artifact.envelope, index_version=index_version)
                    index = projection.build_index_artifact(payload)
                    self.assertEqual(reader._validate_index_envelope(index.envelope), _index_envelope(payload, index_version))
                    entry = next(row for row in payload["entries"] if row["bundle_state"] == "native")
                    observed = reader.ValidatedEvidenceSource("native_bundle", _source_basis(index.envelope, entry), artifact.envelope)
                    self.assertEqual(observed.source, artifact.envelope)
                    self.assertEqual(reference_json_bytes(observed.source) + b"\n", original[0])
                    self.assertEqual((entry["bundle_digest"], entry["file_digest"]), original[1:])

    def test_invalid_bundle_pairs_roots_and_domains_remain_rejected(self):
        accepted = {(source, versions[0]) for source, versions in ORDINARY_FORMATS.items()} | {(29, 3)}
        invalid_payloads = [
            (source, version, _bundle_payload(source, version))
            for source in range(18, 33) for version in BUNDLE_DOMAINS
            if (source, version) not in accepted
        ]
        # Version three requires a real graph; version four requires the root,
        # including on ordinary completion. Old formats reject the added root.
        invalid_payloads.append((29, 3, _bundle_payload(29, 3)))
        for source, version in ((20, 2), (29, 2), (30, 4), (31, 4), (32, 4)):
            payload = _bundle_payload(source, version)
            if version == 2:
                payload["review_continuation_basis"] = None
            else:
                del payload["review_continuation_basis"]
            invalid_payloads.append((source, version, payload))
        for source, version, payload in invalid_payloads:
            with self.subTest(source=source, version=version, keys=tuple(payload)):
                for build in (projection.build_bundle_artifact, projection.bundle_payload_digest):
                    with self.assertRaises(projection.EvidenceProjectionError):
                        build(payload)
                bundle = _bundle_envelope(payload, version)
                index = _index_envelope(_index_payload(31, bundle, index_version=3), 3)
                entry = next(row for row in index["payload"]["entries"] if row["bundle_state"] == "native")
                with self.assertRaises(reader.EvidenceConsumerError):
                    reader.ValidatedEvidenceSource("native_bundle", _source_basis(index, entry), bundle)
        for source, (version, index_version) in ORDINARY_FORMATS.items():
            with self.subTest(wrong_domain_source=source):
                payload = _bundle_payload(source, version)
                bundle = _bundle_envelope(payload, version, domain=BUNDLE_DOMAINS[2 if version == 1 else 1])
                index = _index_envelope(_index_payload(source, bundle, index_version=index_version), index_version)
                entry = next(row for row in index["payload"]["entries"] if row["bundle_state"] == "native")
                with self.assertRaises(reader.EvidenceConsumerError):
                    reader.ValidatedEvidenceSource("native_bundle", _source_basis(index, entry), bundle)

    def test_invalid_index_sources_fields_versions_and_domains_remain_rejected(self):
        bundle = _bundle_envelope(_bundle_payload(31, 4), 4)
        valid = _index_payload(31, bundle, index_version=3)
        for source in (18, 33, -1, None, True, False, "31", 31.0):
            with self.subTest(invalid_source=source):
                payload = {**valid, "source_schema_version": source}
                for build in (projection.build_index_artifact, projection.index_payload_digest):
                    with self.assertRaises(projection.EvidenceProjectionError):
                        build(payload)
                with self.assertRaises(reader.EvidenceConsumerError):
                    reader._validate_index_envelope(_index_envelope(payload, 3))
        for source, (bundle_version, index_version) in ORDINARY_FORMATS.items():
            bundle = _bundle_envelope(_bundle_payload(source, bundle_version), bundle_version)
            payload = _index_payload(source, bundle, index_version=index_version)
            wrong_fields = deepcopy(payload)
            for entry in wrong_fields["entries"]:
                if index_version == 1:
                    entry["bundle_format_version"] = 1 if entry["bundle_state"] == "native" else None
                else:
                    del entry["bundle_format_version"]
            with self.subTest(wrong_fields_source=source):
                for build in (projection.build_index_artifact, projection.index_payload_digest):
                    with self.assertRaises(projection.EvidenceProjectionError):
                        build(wrong_fields)
                with self.assertRaises(reader.EvidenceConsumerError):
                    reader._validate_index_envelope(_index_envelope(wrong_fields, index_version))
            for wrong_version in set(INDEX_DOMAINS) - {index_version}:
                with self.subTest(source=source, wrong_version=wrong_version):
                    with self.assertRaises(reader.EvidenceConsumerError):
                        reader._validate_index_envelope(_index_envelope(payload, wrong_version))
                    with self.assertRaises(reader.EvidenceConsumerError):
                        reader._validate_index_envelope(_index_envelope(payload, index_version, domain=INDEX_DOMAINS[wrong_version]))


if __name__ == "__main__":
    unittest.main()
