from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from tests.evidence_reader_oracle import (  # noqa: E402
    EvidenceConsumerError,
    ValidatedEvidenceSource,
    read_evidence_index,
    revalidate_validated_index,
    revalidate_validated_source,
    validate_evidence_source,
)
from tests.evidence_test_support import (  # noqa: E402
    domain_digest,
    index_entries,
    reference_json_bytes,
    refresh_bundle_seals,
    sealed_bundle,
    valid_native_payload,
    write_evidence_tree,
)
from tests.m14_test_support import tree_snapshot  # noqa: E402


BUNDLE_V2_DOMAIN = b"taskgov-completion-evidence-bundle-v2\0"
INDEX_V2_DOMAIN = b"taskgov-evidence-index-v2\0"


def _source21_not_required_payload() -> dict[str, object]:
    """Reuse the existing schema21 fixture transformation without its module."""

    payload = valid_native_payload()
    payload["source_schema_version"] = 21
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
    return payload


def _write_v2_tree(
    root: Path,
    *,
    preserved_v1: bool = False,
    index_schema_version: int = 21,
) -> tuple[Path, dict, dict]:
    if preserved_v1:
        bundle, document = sealed_bundle()
    else:
        payload = _source21_not_required_payload()
        bundle = {
            "bundle_digest": domain_digest(BUNDLE_V2_DOMAIN, payload),
            "format_version": 2,
            "payload": payload,
        }
        document = reference_json_bytes(bundle) + b"\n"
    entries = index_entries(document, bundle["bundle_digest"])
    for entry in entries:
        entry["bundle_format_version"] = (
            bundle["format_version"] if entry["bundle_state"] == "native" else None
        )
    index_payload = {
        "source_schema_version": index_schema_version,
        "project_id": bundle["payload"]["project_id"],
        "projection_generation": 8,
        "bundle_count": 1,
        "legacy_count": 1,
        "entries": entries,
    }
    index = {
        "format_version": 2,
        "index_digest": domain_digest(INDEX_V2_DOMAIN, index_payload),
        "payload": index_payload,
    }
    evidence = root / "evidence"
    bundles = evidence / "bundles"
    bundles.mkdir(parents=True)
    (bundles / f"{bundle['payload']['bundle_id']}.json").write_bytes(document)
    (evidence / "index.json").write_bytes(reference_json_bytes(index) + b"\n")
    return evidence, bundle, index


def _basis(index: dict, entry: dict) -> dict:
    return {
        "index_format_version": index["format_version"],
        "source_schema_version": index["payload"]["source_schema_version"],
        "project_id": index["payload"]["project_id"],
        "projection_generation": index["payload"]["projection_generation"],
        "index_digest": index["index_digest"],
        "entry": deepcopy(entry),
    }


class EvidenceReaderOracleTests(unittest.TestCase):
    def test_v1_native_and_legacy_are_exact_and_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = write_evidence_tree(Path(temporary))
            bundle, document = sealed_bundle()
            expected_index = json.loads((evidence / "index.json").read_bytes())
            before = tree_snapshot(evidence)
            index = read_evidence_index(
                evidence,
                expected_project_id=bundle["payload"]["project_id"],
            )
            self.assertEqual((index.format_version, index.source_schema_version), (1, 19))
            self.assertEqual(index.entries, tuple(expected_index["payload"]["entries"]))
            for entry in index.entries:
                with self.subTest(state=entry["bundle_state"]):
                    source = validate_evidence_source(index, entry)
                    self.assertEqual(source.source_basis, _basis(expected_index, entry))
                    if entry["bundle_state"] == "native":
                        self.assertEqual(source.source_kind, "native_bundle")
                        self.assertEqual(source.source, bundle)
                        self.assertEqual(reference_json_bytes(source.source) + b"\n", document)
                    else:
                        self.assertEqual(source.source_kind, "legacy_index_entry")
                        self.assertIsNone(source.source)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_v2_preserves_full_native_entry_and_explicit_legacy_absence(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence, bundle, expected_index = _write_v2_tree(Path(temporary))
            before = tree_snapshot(evidence)
            index = read_evidence_index(evidence)
            self.assertEqual((index.format_version, index.source_schema_version), (2, 21))
            self.assertEqual(index.entries, tuple(expected_index["payload"]["entries"]))
            for entry in index.entries:
                with self.subTest(state=entry["bundle_state"]):
                    source = validate_evidence_source(index, entry)
                    self.assertEqual(source.source_basis, _basis(expected_index, entry))
                    if entry["bundle_state"] == "native":
                        self.assertEqual(source.source_basis["entry"]["bundle_format_version"], 2)
                        self.assertEqual(source.source, bundle)
                    else:
                        self.assertIsNone(source.source_basis["entry"]["bundle_format_version"])
                        self.assertIsNone(source.source)
                    checked = revalidate_validated_source(source)
                    self.assertEqual(checked.source_basis, source.source_basis)
                    self.assertEqual(checked.source, source.source)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_v2_index_preserves_unchanged_v1_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence, bundle, expected_index = _write_v2_tree(
                Path(temporary), preserved_v1=True,
            )
            before = tree_snapshot(evidence)
            index = read_evidence_index(evidence)
            entry = next(row for row in index.entries if row["bundle_state"] == "native")
            source = validate_evidence_source(index, entry)
            self.assertEqual(source.source_basis, _basis(expected_index, entry))
            self.assertEqual(source.source_basis["index_format_version"], 2)
            self.assertEqual(source.source_basis["entry"]["bundle_format_version"], 1)
            self.assertEqual(source.source, bundle)
            self.assertEqual(source.source["format_version"], 1)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_membership_and_project_mismatches_are_rejected_without_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence, bundle, expected_index = _write_v2_tree(Path(temporary))
            before = tree_snapshot(evidence)
            foreign_project = "tg_project_" + "f" * 32
            with self.assertRaises(EvidenceConsumerError):
                read_evidence_index(evidence, expected_project_id=foreign_project)
            index = read_evidence_index(evidence)
            entry = next(row for row in index.entries if row["bundle_state"] == "native")
            unlisted = {**entry, "cycle_ordinal": entry["cycle_ordinal"] + 1}
            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(index, unlisted)
            basis = _basis(expected_index, entry)
            basis["project_id"] = foreign_project
            with self.assertRaises(EvidenceConsumerError):
                ValidatedEvidenceSource("native_bundle", basis, bundle)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_source_basis_is_closed_and_preserves_version_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence, bundle, expected_index = _write_v2_tree(Path(temporary))
            before = tree_snapshot(evidence)
            entry = next(
                row for row in expected_index["payload"]["entries"]
                if row["bundle_state"] == "native"
            )
            valid = _basis(expected_index, entry)
            wrong_bundle_version = deepcopy(valid)
            wrong_bundle_version["entry"]["bundle_format_version"] = 1
            missing_version = deepcopy(valid)
            del missing_version["index_format_version"]
            for basis in (
                wrong_bundle_version,
                missing_version,
                {**valid, "index_format_version": 1},
                {**valid, "projection_generation": True},
                {**valid, "unexpected": None},
            ):
                with self.subTest(basis=basis):
                    with self.assertRaises(EvidenceConsumerError):
                        ValidatedEvidenceSource("native_bundle", basis, bundle)
            with self.assertRaises(EvidenceConsumerError):
                ValidatedEvidenceSource("legacy_index_entry", valid, None)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_source_schema_cannot_exceed_the_selected_index_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence, bundle, expected_index = _write_v2_tree(
                Path(temporary), index_schema_version=20,
            )
            before = tree_snapshot(evidence)
            index = read_evidence_index(evidence)
            entry = next(row for row in index.entries if row["bundle_state"] == "native")
            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(index, entry)
            with self.assertRaises(EvidenceConsumerError):
                ValidatedEvidenceSource("native_bundle", _basis(expected_index, entry), bundle)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_index_and_source_return_fresh_copies_and_reject_spoofs(self):
        with tempfile.TemporaryDirectory() as temporary:
            evidence, bundle, expected_index = _write_v2_tree(Path(temporary))
            before = tree_snapshot(evidence)
            index = read_evidence_index(evidence)
            entry = next(row for row in index.entries if row["bundle_state"] == "native")
            source = validate_evidence_source(index, entry)
            expected_basis = _basis(expected_index, entry)
            entry["bundle_format_version"] = 1
            changed_basis = source.source_basis
            changed_basis["entry"]["bundle_format_version"] = 1
            changed_source = source.source
            changed_source["payload"]["task"]["title"] = "Changed copy"
            self.assertEqual(index.entries, tuple(expected_index["payload"]["entries"]))
            self.assertEqual(source.source_basis, expected_basis)
            self.assertEqual(source.source, bundle)
            checked_index = revalidate_validated_index(index)
            self.assertEqual(checked_index.entries, index.entries)
            checked_source = revalidate_validated_source(source)
            self.assertEqual(checked_source.source_basis, expected_basis)
            index_spoof = SimpleNamespace(entries=index.entries, evidence_root=evidence)
            source_spoof = SimpleNamespace(
                source_kind=source.source_kind, source_basis=expected_basis, source=bundle,
            )
            with self.assertRaises(EvidenceConsumerError):
                revalidate_validated_index(index_spoof)
            with self.assertRaises(EvidenceConsumerError):
                validate_evidence_source(index_spoof, expected_basis["entry"])
            with self.assertRaises(EvidenceConsumerError):
                revalidate_validated_source(source_spoof)
            self.assertEqual(tree_snapshot(evidence), before)

    def test_reader_imports_are_isolated_from_producers_and_analyzer(self):
        # Fixture imports above may load the M22 producer. This fresh interpreter
        # checks the reader alone, with its one allowed runtime I/O dependency.
        script = textwrap.dedent("""
            import importlib.abc
            import sys

            sys.path[:0] = sys.argv[1:]

            def forbidden(name):
                return (
                    name in {"sqlite3", "_sqlite3"}
                    or name.startswith("sqlite3.")
                    or (
                        name.startswith("task_governance_tool.")
                        and name != "task_governance_tool.state_paths"
                    )
                    or name in {"tests.evidence_test_support", "tests.m23_test_support"}
                )

            class Reject(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if forbidden(fullname):
                        raise AssertionError("forbidden reader import: " + fullname)
                    return None

            sys.meta_path.insert(0, Reject())
            import tests.evidence_reader_codec as codec
            import tests.evidence_reader_oracle as reader
            assert codec.canonical_json_bytes({"v": 1}) == b'{"v":1}'
            assert callable(reader.read_evidence_index)
            assert not hasattr(reader, "revalidate_descriptor_source")
            assert not any(forbidden(name) for name in sys.modules)
        """)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script, str(ROOT), str(SCRIPTS_ROOT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
