from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests.m14_test_support import tree_snapshot
from tests.evidence_test_support import (
    BUNDLE_DOMAIN,
    INDEX_DOMAIN,
    REVIEW_PROVENANCE_DOMAIN,
    domain_digest,
    reference_json_bytes,
    refresh_inner_digests,
    valid_native_payload,
    write_evidence_tree,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests.evidence_reader_oracle import (  # noqa: E402
    EvidenceConsumerError,
    ValidatedEvidenceIndex,
    read_evidence_index,
    validate_evidence_source,
)


class EvidenceReaderRegressionTests(unittest.TestCase):
    def _reseal_bundle(self, evidence: Path, mutate) -> dict:
        index_path = evidence / "index.json"
        index = json.loads(index_path.read_bytes())
        entry = next(
            item
            for item in index["payload"]["entries"]
            if item["bundle_state"] == "native"
        )
        bundle_path = evidence.joinpath(*entry["bundle_file"].split("/"))
        bundle = json.loads(bundle_path.read_bytes())
        mutate(bundle["payload"])
        bundle["bundle_digest"] = domain_digest(
            BUNDLE_DOMAIN,
            bundle["payload"],
        )
        document = reference_json_bytes(bundle) + b"\n"
        bundle_path.write_bytes(document)
        entry["bundle_digest"] = bundle["bundle_digest"]
        entry["file_digest"] = "sha256:" + hashlib.sha256(document).hexdigest()
        index["index_digest"] = domain_digest(INDEX_DOMAIN, index["payload"])
        index_path.write_bytes(reference_json_bytes(index) + b"\n")
        return entry

    def _rewrite_index(self, evidence: Path, mutate) -> None:
        index_path = evidence / "index.json"
        index = json.loads(index_path.read_bytes())
        mutate(index["payload"])
        index["index_digest"] = domain_digest(INDEX_DOMAIN, index["payload"])
        index_path.write_bytes(reference_json_bytes(index) + b"\n")

    def test_native_and_legacy_sources_are_exact_and_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            before = tree_snapshot(evidence)
            expected_project = str(valid_native_payload()["project_id"])
            index = read_evidence_index(
                evidence,
                expected_project_id=expected_project,
            )
            sources = [
                validate_evidence_source(index, entry) for entry in index.entries
            ]
            self.assertEqual(
                [source.source_kind for source in sources],
                ["legacy_index_entry", "native_bundle"],
            )
            self.assertIsNone(sources[0].source)
            self.assertEqual(
                sources[1].source["payload"]["bundle_id"],
                sources[1].source_basis["entry"]["bundle_id"],
            )
            self.assertEqual(tree_snapshot(evidence), before)

    def test_validated_index_owns_canonical_bytes_and_rejects_spoofs(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            index = read_evidence_index(evidence)
            original_entries = index.entries
            legacy = next(
                item for item in original_entries if item["bundle_state"] == "legacy_unknown"
            )
            validate_evidence_source(index, legacy)

            leaked_entries = index.entries
            leaked_legacy = next(
                item for item in leaked_entries if item["bundle_state"] == "legacy_unknown"
            )
            leaked_legacy["task_id"] = "tg_task_1111111111111111"
            self.assertEqual(index.entries, original_entries)
            before = tree_snapshot(evidence)
            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(index, leaked_legacy)
            with self.assertRaises(EvidenceConsumerError):
                ValidatedEvidenceIndex(
                    evidence,
                    index.project_id,
                    index.projection_generation,
                    index.index_digest,
                    leaked_entries,
                )

            class IndexSpoof:
                evidence_root = index.evidence_root
                project_id = index.project_id
                projection_generation = index.projection_generation
                index_digest = index.index_digest
                entries = index.entries

            spoof = IndexSpoof()
            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(spoof, legacy)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_project_id_grammar_accepts_both_current_families_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            index = read_evidence_index(evidence)
            entries = index.entries
            legacy_project_id = "sample-project-abcdef123456"
            payload = {
                "source_schema_version": 19,
                "project_id": legacy_project_id,
                "projection_generation": index.projection_generation,
                "bundle_count": 1,
                "legacy_count": 1,
                "entries": list(entries),
            }
            accepted = ValidatedEvidenceIndex(
                evidence,
                legacy_project_id,
                index.projection_generation,
                domain_digest(INDEX_DOMAIN, payload),
                entries,
            )
            self.assertEqual(accepted.project_id, legacy_project_id)

            payload["project_id"] = "arbitrary-project"
            with self.assertRaises(EvidenceConsumerError):
                ValidatedEvidenceIndex(
                    evidence,
                    payload["project_id"],
                    index.projection_generation,
                    domain_digest(INDEX_DOMAIN, payload),
                    entries,
                )

    def test_inner_digest_vectors_are_literal_and_independent(self):
        payload = valid_native_payload()
        acceptance = next(
            item for item in payload["criteria"] if item["kind"] == "acceptance"
        )
        artifact_reference = next(
            item
            for item in payload["evidence_references"]
            if item["source_kind"] == "artifact_manifest"
        )
        self.assertEqual(
            acceptance["digest"],
            "sha256:5102128c3cf3118d95daf3537251faae564af8bf70cd3dd02cf393be75bf7cbf",
        )
        self.assertEqual(
            payload["artifact_manifest"]["digest"],
            "sha256:297ac6f6118b65c5f00ba59b64d5111aaa727dc3c176420eeb91fd5c5cb42de2",
        )
        self.assertEqual(
            artifact_reference["digest"],
            "sha256:f94f0d079d85964285718631da6b53d71ce96d3ad12b8d03c8a1ad25a9b90461",
        )

    def test_duplicate_or_noncanonical_index_is_rejected_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            index_path = evidence / "index.json"
            original = index_path.read_bytes()
            index_path.write_bytes(
                original.replace(
                    b'"format_version":1',
                    b'"format_version":1,"format_version":1',
                    1,
                )
            )
            before = tree_snapshot(evidence)
            with self.assertRaises(EvidenceConsumerError) as raised:
                read_evidence_index(evidence)
            self.assertEqual(raised.exception.code, "source_invalid")
            self.assertEqual(tree_snapshot(evidence), before)

    def test_referenced_bundle_tamper_and_unlisted_entry_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            index = read_evidence_index(evidence)
            native = next(
                entry for entry in index.entries if entry["bundle_state"] == "native"
            )
            unlisted = dict(native)
            unlisted["cycle_ordinal"] = 99
            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(index, unlisted)

            bundle_path = evidence.joinpath(*native["bundle_file"].split("/"))
            bundle_path.write_bytes(bundle_path.read_bytes() + b" ")
            before = tree_snapshot(evidence)
            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(index, native)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_resealed_semantic_tamper_is_source_invalid_without_write(self):
        mutations = {
            "artifact_target_dispatch": lambda payload: payload[
                "artifact_manifest"
            ].update({"state": "opaque_target"}),
            "link_attribution": lambda payload: payload["criterion_links"][0].update(
                {"producer_class": "external_system"}
            ),
            "reference_target_binding": lambda payload: payload[
                "evidence_references"
            ][0].update({"target_generation": payload["target"]["generation"] + 1}),
            "review_gate_basis": lambda payload: payload["review_receipts"].clear(),
            "verification_subject_binding": lambda payload: payload[
                "verification_receipt"
            ]["verification_subject"].update(
                {"authority_snapshot_id": "tg_authority_snapshot_0000000000000000"}
            ),
            "completion_matrix": lambda payload: payload[
                "completion_evidence"
            ].update({"completion_commit_required": 1}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                evidence = write_evidence_tree(Path(temporary))
                self._reseal_bundle(evidence, mutate)
                index = read_evidence_index(evidence)
                native = next(
                    entry
                    for entry in index.entries
                    if entry["bundle_state"] == "native"
                )
                before = tree_snapshot(evidence)
                with self.assertRaises(EvidenceConsumerError) as raised:
                    validate_evidence_source(index, native)
                self.assertEqual(raised.exception.code, "source_invalid")
                self.assertEqual(tree_snapshot(evidence), before)

    def test_resealed_artifact_path_matrix_and_order_tamper_is_rejected(self):
        def add_entry(
            path: str,
            *,
            ordinal: int = 0,
            mode: str = "100644",
            object_id: str = "a" * 40,
        ) -> dict[str, object]:
            return {
                "ordinal": ordinal,
                "kind": "add",
                "old_path": None,
                "new_path": path,
                "before_mode": None,
                "before_object_id": None,
                "after_mode": mode,
                "after_object_id": object_id,
            }

        def set_entries(payload, entries) -> None:
            payload["artifact_manifest"]["entries"] = entries
            refresh_inner_digests(payload)

        cases = {
            "unsafe_path": lambda payload: set_entries(
                payload,
                [add_entry("../secret.txt")],
            ),
            "null_matrix": lambda payload: set_entries(
                payload,
                [
                    {
                        **add_entry("safe.txt"),
                        "old_path": "old.txt",
                        "before_mode": "100644",
                        "before_object_id": "b" * 40,
                    }
                ],
            ),
            "invalid_mode": lambda payload: set_entries(
                payload,
                [add_entry("safe.txt", mode="100600")],
            ),
            "invalid_object_id": lambda payload: set_entries(
                payload,
                [add_entry("safe.txt", object_id="0" * 40)],
            ),
            "noncanonical_order": lambda payload: set_entries(
                payload,
                [add_entry("b.txt", ordinal=0), add_entry("a.txt", ordinal=1)],
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                evidence = write_evidence_tree(Path(temporary))
                entry = self._reseal_bundle(evidence, mutate)
                index = read_evidence_index(evidence)
                before = tree_snapshot(evidence)
                with self.assertRaises(EvidenceConsumerError) as raised:
                    validate_evidence_source(index, entry)
                self.assertEqual(raised.exception.code, "source_invalid")
                self.assertEqual(tree_snapshot(evidence), before)

    def test_literal_valid_artifact_entry_is_accepted_read_only(self):
        def add_literal_entry(payload) -> None:
            payload["artifact_manifest"]["entries"] = [
                {
                    "ordinal": 0,
                    "kind": "add",
                    "old_path": None,
                    "new_path": "safe.txt",
                    "before_mode": None,
                    "before_object_id": None,
                    "after_mode": "100644",
                    "after_object_id": "a" * 40,
                }
            ]
            refresh_inner_digests(payload)

        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            entry = self._reseal_bundle(evidence, add_literal_entry)
            index = read_evidence_index(evidence)
            before = tree_snapshot(evidence)
            source = validate_evidence_source(index, entry)
            self.assertEqual(
                source.source["payload"]["artifact_manifest"]["entries"][0][
                    "new_path"
                ],
                "safe.txt",
            )
            self.assertEqual(tree_snapshot(evidence), before)

    def test_resealed_private_task_text_is_rejected_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            entry = self._reseal_bundle(
                evidence,
                lambda payload: payload["task"].update(
                    {"title": "Authorization: Bearer secret-value"}
                ),
            )
            index = read_evidence_index(evidence)
            before = tree_snapshot(evidence)
            with self.assertRaises(EvidenceConsumerError) as raised:
                validate_evidence_source(index, entry)
            self.assertEqual(raised.exception.code, "source_invalid")
            self.assertEqual(tree_snapshot(evidence), before)

    def test_privacy_parity_rejects_stack_and_inline_raw_output_but_allows_null(self):
        def stack_trace(payload) -> None:
            payload["task"]["description"] = "stack trace:\n#0 secret-frame"
            refresh_inner_digests(payload)

        def inline_raw_output(payload) -> None:
            payload["review_receipts"][0]["summary"] = (
                "review observed raw stdout dump secret"
            )
            refresh_inner_digests(payload)

        for name, mutate in (
            ("stack_trace", stack_trace),
            ("inline_raw_output", inline_raw_output),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                evidence = write_evidence_tree(Path(temporary))
                entry = self._reseal_bundle(evidence, mutate)
                index = read_evidence_index(evidence)
                before = tree_snapshot(evidence)
                with self.assertRaises(EvidenceConsumerError):
                    validate_evidence_source(index, entry)
                self.assertEqual(tree_snapshot(evidence), before)

        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))

            def safe_null(payload) -> None:
                payload["task"]["description"] = 'safe schema example: {"token": null}'
                refresh_inner_digests(payload)

            entry = self._reseal_bundle(evidence, safe_null)
            index = read_evidence_index(evidence)
            before = tree_snapshot(evidence)
            source = validate_evidence_source(index, entry)
            self.assertIn('"token": null', source.source["payload"]["task"]["description"])
            self.assertEqual(tree_snapshot(evidence), before)

    def test_numeric_metadata_policy_survives_independent_bundle_read(self):
        accepted = (
            "max_tokens=4096 token_count=1024 password_length=12",
            "Metadata (`max_tokens = 004096`); token_count=0] password_length=12}",
        )
        rejected = (
            "max_tokens=4096 token=secret-value",
            "token=secret-value password_length=12",
            "token=max_tokens=4096",
            "max_tokens=4096 Authorization: Bearer secret-value",
            "max_tokens=4096\nTraceback (most recent call last)",
            "max_tokens=4096secret",
            "token_count=1.5",
            "password_length=-12",
            'password_length="12"',
            "token_count=1e3",
            "max_tokens=\u0664\u0660\u0669\u0666",
            "api_token_count=1024",
            "password_length_hint=12",
            "MAX_TOKENS=4096",
            ".max_tokens=4096",
            "-token_count=1024",
            "--password_length=12",
            "Fix token=removed; max_tokens=4096",
        )
        for value in (*accepted, *rejected):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                evidence = write_evidence_tree(Path(temporary))

                def update_description(payload):
                    payload["task"]["description"] = value
                    refresh_inner_digests(payload)

                entry = self._reseal_bundle(evidence, update_description)
                index = read_evidence_index(evidence)
                before = tree_snapshot(evidence)
                if value in accepted:
                    source = validate_evidence_source(index, entry)
                    self.assertEqual(source.source["payload"]["task"]["description"], value)
                else:
                    with self.assertRaises(EvidenceConsumerError) as raised:
                        validate_evidence_source(index, entry)
                    self.assertEqual(raised.exception.code, "source_invalid")
                self.assertEqual(tree_snapshot(evidence), before)

    def test_timestamp_routes_require_real_canonical_utc_seconds(self):
        def mutate_review_created_at(value: str):
            def mutate(payload) -> None:
                payload["review_receipts"][0]["created_at"] = value
                refresh_inner_digests(payload)

            return mutate

        for name, value in (
            ("secret_shaped", "Authorization: Bearer secret-value"),
            ("nonexistent_date", "2026-02-30T00:00:00Z"),
            ("fractional_second", "2026-08-05T00:01:00.000Z"),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                evidence = write_evidence_tree(Path(temporary))
                entry = self._reseal_bundle(
                    evidence,
                    mutate_review_created_at(value),
                )
                index = read_evidence_index(evidence)
                before = tree_snapshot(evidence)
                with self.assertRaises(EvidenceConsumerError):
                    validate_evidence_source(index, entry)
                self.assertEqual(tree_snapshot(evidence), before)

        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))

            def invalid_verification_timestamp(payload) -> None:
                payload["verification_receipt"]["created_at"] = (
                    "2026-08-04T00:00:00+00:00"
                )
                refresh_inner_digests(payload)

            entry = self._reseal_bundle(evidence, invalid_verification_timestamp)
            index = read_evidence_index(evidence)
            before = tree_snapshot(evidence)
            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(index, entry)
            self.assertEqual(tree_snapshot(evidence), before)

        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            self._rewrite_index(
                evidence,
                lambda payload: next(
                    item
                    for item in payload["entries"]
                    if item["bundle_state"] == "native"
                ).update({"sealed_at": "2026-13-01T00:00:00Z"}),
            )
            before = tree_snapshot(evidence)
            with self.assertRaises(EvidenceConsumerError):
                read_evidence_index(evidence)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_privacy_guard_preserves_legacy_numeric_contract_constraint(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            entry = self._reseal_bundle(
                evidence,
                lambda payload: payload["contract"].update(
                    {"constraints": "dispatch_authorization=1"}
                ),
            )
            index = read_evidence_index(evidence)
            before = tree_snapshot(evidence)
            source = validate_evidence_source(index, entry)
            self.assertEqual(
                source.source["payload"]["contract"]["constraints"],
                "dispatch_authorization=1",
            )
            self.assertEqual(tree_snapshot(evidence), before)

    def test_resealed_inner_digest_substitution_is_rejected_without_write(self):
        mutations = {
            "criterion": lambda payload: payload["criteria"][0].update(
                {"digest": "sha256:" + ("0" * 64)}
            ),
            "artifact_manifest": lambda payload: payload[
                "artifact_manifest"
            ].update({"digest": "sha256:" + ("1" * 64)}),
            "evidence_reference": lambda payload: payload[
                "evidence_references"
            ][0].update({"digest": "sha256:" + ("2" * 64)}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                evidence = write_evidence_tree(Path(temporary))
                self._reseal_bundle(evidence, mutate)
                index = read_evidence_index(evidence)
                native = next(
                    entry
                    for entry in index.entries
                    if entry["bundle_state"] == "native"
                )
                before = tree_snapshot(evidence)
                with self.assertRaises(EvidenceConsumerError) as raised:
                    validate_evidence_source(index, native)
                self.assertEqual(raised.exception.code, "source_invalid")
                self.assertEqual(tree_snapshot(evidence), before)

    def test_resealed_review_provenance_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))

            def refresh_provenance_digest(payload) -> None:
                receipt = payload["review_receipts"][0]
                provenance = receipt["review_provenance"]
                sealed_target = dict(payload["target"])
                sealed_target["base_revision"] = ""
                digest_payload = {
                    "project_id": payload["project_id"],
                    "task_id": payload["task"]["task_id"],
                    "review_receipt_id": receipt["review_receipt_id"],
                    "receipt_kind": receipt["receipt_kind"],
                    "target": sealed_target,
                    **{
                        key: value
                        for key, value in provenance.items()
                        if key not in {"review_provenance_id", "digest"}
                    },
                }
                provenance["digest"] = domain_digest(
                    REVIEW_PROVENANCE_DOMAIN,
                    digest_payload,
                )
                refresh_inner_digests(payload)

            def make_tier_one(payload):
                payload["task"]["review_tier"] = 1
                receipt = payload["review_receipts"][0]
                receipt.update(
                    {
                        "reviewer_key": "independent-reviewer",
                        "receipt_kind": "independent",
                        "verdict": "pass",
                        "summary": "Independent review passed.",
                        "user_approved": 0,
                    }
                )
                provenance = {
                    "review_provenance_id": "tg_review_provenance_1234567890abcdef",
                    "provenance_version": 1,
                    "reviewer_class": "llm",
                    "model_state": "declared",
                    "declared_model_id": "gpt-5.6",
                    "skill_state": "not_used",
                    "declared_skill_id": None,
                    "declared_skill_version": None,
                    "review_profiles": ["general"],
                    "review_lenses": ["correctness"],
                    "context_relation": "fresh_context",
                    "method_codes": ["source_inspection"],
                    "assurance_class": "bound_attestation",
                    "producer_class": "trusted_caller",
                    "producer_version": 1,
                    "digest": None,
                }
                receipt["review_provenance"] = provenance
                refresh_provenance_digest(payload)

            self._reseal_bundle(evidence, make_tier_one)
            index = read_evidence_index(evidence)
            native = next(
                entry for entry in index.entries if entry["bundle_state"] == "native"
            )
            validate_evidence_source(index, native)

            self._reseal_bundle(
                evidence,
                lambda payload: payload["review_receipts"][0][
                    "review_provenance"
                ].update({"context_relation": "same_context"}),
            )
            index = read_evidence_index(evidence)
            native = next(
                entry for entry in index.entries if entry["bundle_state"] == "native"
            )
            before = tree_snapshot(evidence)
            with self.assertRaises(EvidenceConsumerError) as raised:
                validate_evidence_source(index, native)
            self.assertEqual(raised.exception.code, "source_invalid")
            self.assertEqual(tree_snapshot(evidence), before)

            def private_declared_model(payload) -> None:
                provenance = payload["review_receipts"][0]["review_provenance"]
                provenance["context_relation"] = "fresh_context"
                provenance["declared_model_id"] = "Authorization:Bearer-secret"
                refresh_provenance_digest(payload)

            entry = self._reseal_bundle(evidence, private_declared_model)
            index = read_evidence_index(evidence)
            before = tree_snapshot(evidence)
            with self.assertRaises(EvidenceConsumerError) as raised:
                validate_evidence_source(index, entry)
            self.assertEqual(raised.exception.code, "source_invalid")
            self.assertEqual(tree_snapshot(evidence), before)


if __name__ == "__main__":
    unittest.main()
