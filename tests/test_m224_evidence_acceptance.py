from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tests.m14_test_support import (
    json_payload,
    make_physical_install,
    tree_snapshot,
)
from tests.evidence_test_support import (
    refresh_inner_digests,
    v1_native_payload,
    write_evidence_tree,
)
from tests.evidence_reader_oracle import (
    EvidenceConsumerError,
    read_evidence_index,
    validate_evidence_source,
)
from tests.m214c_test_support import inject_contract_pointer_fault
from tests.m223_test_support import logical_database_digest
from tests.m224_report_consumer import (
    EvidenceReportError,
    read_evidence_report,
)
from tests.review_test_helpers import (
    NOT_REQUIRED_REVIEW_PROVENANCE_CASE,
    REVIEW_PROVENANCE_V1_CASES,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"

import sys

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool.evidence_validation_repository import (  # noqa: E402
    capture_evidence_projection_basis,
)
from task_governance_tool.storage import (  # noqa: E402
    StorageError,
    connect_readonly,
)


VERIFICATION_1000 = "界" * 1_000
COMMON_CODE_COUNT = len(REVIEW_PROVENANCE_V1_CASES) + 5
PROJECTED_CODE_COUNT = len(REVIEW_PROVENANCE_V1_CASES) + 5
# Literal test expectations; never obtain domains from the producing selector.
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
REVIEW_PROVENANCE_DOMAIN = b"taskgov-review-provenance-v1\0"
PROVENANCE_DIGEST_FIELDS = (
    "provenance_version",
    "reviewer_class",
    "model_state",
    "declared_model_id",
    "skill_state",
    "declared_skill_id",
    "declared_skill_version",
    "review_profiles",
    "review_lenses",
    "context_relation",
    "method_codes",
    "assurance_class",
    "producer_class",
    "producer_version",
)
COUNTED_TABLES = (
    "tasks",
    "task_events",
    "review_receipts",
    "review_receipt_provenance",
    "review_receipt_provenance_codes",
    "evidence_references",
)


def require_cli_json(install, *arguments: str) -> dict[str, object]:
    result = install.run(*arguments, "--json")
    if result.returncode != 0:
        raise AssertionError(result.stdout or result.stderr)
    return json_payload(result)


def database_snapshot(db_path: Path) -> tuple[str, tuple[int, ...]]:
    with closing(connect_readonly(db_path)) as connection:
        digest = logical_database_digest(connection)
        counts = tuple(
            int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{table_name}"'
                ).fetchone()[0]
            )
            for table_name in COUNTED_TABLES
        )
    return digest, counts


def add_contract_task(
    install,
    *,
    title: str,
    review_tier: int,
    verification: str = "",
) -> str:
    arguments = [
        "task",
        "add",
        "--title",
        title,
        "--status",
        "in_progress",
        "--review-tier",
        str(review_tier),
        "--contract-scope",
        "Exercise the focused M22.4 Evidence acceptance boundary.",
        "--contract-acceptance",
        "Public, stored, Bundle, and JSON evidence retain the same meaning.",
        "--contract-constraints",
        "Use only temporary local state and do not infer assurance.",
        "--contract-authority-ref",
        "docs/execution-contracts/tg-m22-evidence-ledger.md#tg-m22-sequence",
    ]
    if verification:
        arguments.extend(["--verification", verification])
    else:
        arguments.extend([
            "--verification-not-required",
            "This fixture exercises review provenance without a verification criterion.",
        ])
    payload = require_cli_json(install, *arguments)
    return str(payload["data"]["task"]["task_id"])


def set_target(install, task_id: str, marker: str) -> int:
    payload = require_cli_json(
        install,
        "review",
        "target",
        "set",
        task_id,
        "--kind",
        "diff_fingerprint",
        "--revision",
        "sha256:" + (marker * 64),
    )
    return int(payload["data"]["task"]["review_target_generation"])


def add_case_receipt(install, task_id: str, case, *, reviewer: str) -> dict:
    verdict = "not_required" if case.receipt_kind == "not_required" else "pass"
    payload = require_cli_json(
        install,
        "review",
        "receipt",
        "add",
        task_id,
        "--reviewer",
        reviewer,
        "--kind",
        case.receipt_kind,
        "--verdict",
        verdict,
        "--summary",
        f"M22.4 {case.name} acceptance",
        *case.cli_options(),
    )
    return payload["data"]["receipt"]


def add_fallback_receipt(
    install,
    task_id: str,
    case,
    *,
    reviewer: str,
    user_approved: bool,
) -> dict:
    arguments = [
        "review",
        "receipt",
        "add",
        task_id,
        "--reviewer",
        reviewer,
        "--kind",
        "self_review_fallback",
        "--verdict",
        "pass",
        "--summary",
        "M22.4 focused fallback acceptance",
        *case.cli_options(),
    ]
    if user_approved:
        arguments.append("--user-approved")
    payload = require_cli_json(install, *arguments)
    return payload["data"]["receipt"]


def complete_task(
    install,
    task_id: str,
) -> dict:
    arguments = [
        "task",
        "complete",
        task_id,
        "--verification-complete",
        "--review-complete",
        "--commit-not-required",
    ]
    return require_cli_json(install, *arguments)


def load_native_bundles(evidence_root: Path) -> tuple[dict, dict[str, dict]]:
    index = json.loads((evidence_root / "index.json").read_text(encoding="utf-8"))
    bundles: dict[str, dict] = {}
    for entry in index["payload"]["entries"]:
        if entry["bundle_state"] != "native":
            continue
        bundle_path = evidence_root.joinpath(*entry["bundle_file"].split("/"))
        bundles[str(entry["task_id"])] = json.loads(
            bundle_path.read_text(encoding="utf-8")
        )
    return index, bundles


def canonical_json_bytes(value: object) -> bytes:
    """Encode a test-owned canonical document without production helpers."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def domain_digest(domain: bytes, value: object) -> str:
    return "sha256:" + hashlib.sha256(
        domain + canonical_json_bytes(value)
    ).hexdigest()


def reseal_evidence_tree(
    evidence_root: Path,
    index: dict,
    bundle: dict | None = None,
    *,
    bundle_domain: bytes | None = None,
    index_domain: bytes | None = None,
) -> None:
    """Seal a test mutation with each envelope's own literal digest domain."""

    if bundle is not None:
        payload = bundle["payload"]
        entry = next(
            item for item in index["payload"]["entries"]
            if item["bundle_id"] == payload["bundle_id"]
        )
        bundle["bundle_digest"] = domain_digest(
            BUNDLE_DOMAINS[bundle["format_version"]]
            if bundle_domain is None else bundle_domain,
            payload,
        )
        document = canonical_json_bytes(bundle) + b"\n"
        evidence_root.joinpath(*entry["bundle_file"].split("/")).write_bytes(document)
        entry["bundle_digest"] = bundle["bundle_digest"]
        entry["file_digest"] = "sha256:" + hashlib.sha256(document).hexdigest()
    index["index_digest"] = domain_digest(
        INDEX_DOMAINS[index["format_version"]]
        if index_domain is None else index_domain,
        index["payload"],
    )
    (evidence_root / "index.json").write_bytes(canonical_json_bytes(index) + b"\n")


def write_versioned_report_tree(
    root: Path,
    *,
    index_source: int = 31,
    index_format: int = 3,
    bundle_source: int = 31,
    bundle_format: int = 4,
) -> tuple[Path, dict, dict]:
    """Transform only a temporary copy of the retained source-19 fixture."""

    evidence_root = write_evidence_tree(root)
    index, bundles = load_native_bundles(evidence_root)
    bundle = next(iter(bundles.values()))
    bundle["format_version"] = bundle_format
    payload = bundle["payload"]
    payload["source_schema_version"] = bundle_source
    payload["bundle_version"] = bundle_format
    if bundle_format != 1:
        payload["verification_basis"] = {
            "basis_version": 1,
            "kind": "caller_attestation",
            "verification_receipt_id": payload["verification_receipt"][
                "verification_receipt_id"
            ],
            "runner_observation_id": None,
        }
        payload["runner_observation"] = None
    if bundle_format in (3, 4):
        payload["review_continuation_basis"] = None
    index["format_version"] = index_format
    index["payload"]["source_schema_version"] = index_source
    if index_format != 1:
        for entry in index["payload"]["entries"]:
            entry["bundle_format_version"] = (
                bundle_format if entry["bundle_state"] == "native" else None
            )
    reseal_evidence_tree(evidence_root, index, bundle)
    return evidence_root, index, bundle


def expected_v1_provenance(case, recorded: dict) -> dict[str, object]:
    """Build literal semantics from the shared oracle plus recorded identity."""

    semantics = case.expected_public_semantics()
    if semantics is None:
        raise AssertionError("expected a v1 Review provenance case")
    return {
        "review_provenance_id": recorded["review_provenance_id"],
        **semantics,
        "digest": recorded["digest"],
    }


def expected_report_receipt(receipt_id: str, case) -> dict[str, object]:
    """Project one selected Receipt without consulting production selection."""

    semantics = case.expected_normalized()
    if semantics is None:
        raise AssertionError("expected a v1 Review provenance case")
    return {
        "review_receipt_id": receipt_id,
        "receipt_kind": "independent",
        "verdict": "pass",
        "provenance_state": "v1",
        **semantics,
    }


def reseal_review_provenance(payload: dict, receipt: dict) -> None:
    provenance = receipt["review_provenance"]
    if provenance is None:
        return
    digest_target = dict(payload["target"])
    if digest_target["base_revision"] is None:
        digest_target["base_revision"] = ""
    digest_payload = {
        "project_id": payload["project_id"],
        "task_id": payload["task"]["task_id"],
        "review_receipt_id": receipt["review_receipt_id"],
        "receipt_kind": receipt["receipt_kind"],
        "target": digest_target,
        **{
            field: provenance[field]
            for field in PROVENANCE_DIGEST_FIELDS
        },
    }
    provenance["digest"] = domain_digest(
        REVIEW_PROVENANCE_DOMAIN,
        digest_payload,
    )


def clone_review_receipt(payload: dict, receipt: dict, *, reviewer: str) -> dict:
    cloned = deepcopy(receipt)
    cloned["review_receipt_id"] = "tg_review_receipt_eeeeeeeeeeeeeeee"
    cloned["reviewer_key"] = reviewer
    provenance = cloned["review_provenance"]
    if provenance is not None:
        provenance["review_provenance_id"] = (
            "tg_review_provenance_dddddddddddddddd"
        )
        reseal_review_provenance(payload, cloned)
    return cloned


def rewrite_gate_basis(
    evidence_root: Path,
    *,
    task_id: str,
    mutation: str,
) -> None:
    """Apply one semantic gate-basis fault and independently reseal the tree."""

    index_path = evidence_root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = next(
        item
        for item in index["payload"]["entries"]
        if item["task_id"] == task_id
    )
    bundle_path = evidence_root.joinpath(*entry["bundle_file"].split("/"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    payload = bundle["payload"]
    receipts = payload["review_receipts"]

    if mutation == "empty":
        receipts.clear()
    elif mutation == "changes_requested":
        receipts[0]["verdict"] = "changes_requested"
    elif mutation == "excess":
        receipts.append(
            clone_review_receipt(
                payload,
                receipts[0],
                reviewer="zz-m224-excess-reviewer",
            )
        )
    elif mutation == "single":
        del receipts[1:]
    elif mutation == "duplicate_reviewer":
        receipts[1]["reviewer_key"] = receipts[0]["reviewer_key"]
    elif mutation == "reversed":
        receipts.reverse()
    elif mutation == "mixed_fallback":
        receipts[1]["receipt_kind"] = "self_review_fallback"
        receipts[1]["user_approved"] = 1
        receipts[1]["summary"] = "Fallback cannot mix with independent basis."
        reseal_review_provenance(payload, receipts[1])
    elif mutation == "tier1_approved_fallback":
        receipts[0]["user_approved"] = 1
    elif mutation == "tier2_unapproved_fallback":
        receipts[0]["user_approved"] = 0
    else:
        raise AssertionError(f"unsupported test mutation: {mutation}")

    reseal_evidence_tree(evidence_root, index, bundle)


class M224ReportVersionTests(unittest.TestCase):
    def assert_invalid_readonly(self, evidence_root: Path, project_id: str) -> None:
        before = tree_snapshot(evidence_root)
        with self.assertRaises(EvidenceReportError) as rejected:
            read_evidence_report(evidence_root, expected_project_id=project_id)
        self.assertEqual(rejected.exception.code, "evidence_report_invalid")
        self.assertEqual(str(rejected.exception), "Evidence report input is invalid")
        self.assertEqual(tree_snapshot(evidence_root), before)

    def test_whole_index_work_is_constant_for_legacy_and_mixed_reports(self):
        from tests import evidence_reader_oracle as oracle

        operation_counts = []
        for include_native in (False, True):
            for legacy_count in (1, 32):
                with self.subTest(native=include_native, legacy=legacy_count), tempfile.TemporaryDirectory() as temporary:
                    evidence, index, _ = write_versioned_report_tree(Path(temporary))
                    entries = index["payload"]["entries"]
                    legacy = next(row for row in entries if row["bundle_state"] == "legacy_unknown")
                    selected = [row for row in entries if row["bundle_state"] == "native"] if include_native else []
                    selected.extend({
                        **legacy,
                        "task_id": f"tg_task_{ordinal:016x}",
                        "completion_cycle_id": f"tg_completion_cycle_{ordinal:016x}",
                    } for ordinal in range(legacy_count))
                    selected.sort(key=lambda row: (row["task_id"], row["cycle_ordinal"], row["completion_cycle_id"]))
                    index["payload"].update(
                        entries=selected, bundle_count=int(include_native), legacy_count=legacy_count,
                    )
                    reseal_evidence_tree(evidence, index)
                    before = tree_snapshot(evidence)
                    with patch.object(
                        oracle, "_validate_index_envelope", wraps=oracle._validate_index_envelope,
                    ) as validation, patch.object(
                        oracle.ValidatedEvidenceIndex, "_fresh_envelope", autospec=True,
                        side_effect=oracle.ValidatedEvidenceIndex._fresh_envelope,
                    ) as decoding:
                        report = read_evidence_report(evidence)
                    self.assertEqual((report["bundle_count"], report["legacy_count"]),
                                     (int(include_native), legacy_count))
                    self.assertEqual(len(report["entries"]), len(selected))
                    self.assertEqual(tree_snapshot(evidence), before)
                    operation_counts.append((validation.call_count, decoding.call_count))
        self.assertGreater(operation_counts[0][0], 0)
        self.assertGreater(operation_counts[0][1], 0)
        self.assertEqual(len(set(operation_counts)), 1, operation_counts)

    def test_resealed_receipt_character_limits_preserve_boundary_and_rejection(self):
        for source, bundle_format, index_format in ((19, 1, 1), (29, 2, 2), (31, 4, 3)):
            for provenance in (None, "v1"):
                for field, limit in (("reviewer_key", 500), ("summary", 1_000)):
                    for length in (limit, limit + 1):
                        with self.subTest(source=source, provenance=provenance, field=field, length=length), tempfile.TemporaryDirectory() as temporary:
                            evidence, index, bundle = write_versioned_report_tree(
                                Path(temporary), index_source=source, index_format=index_format,
                                bundle_source=source, bundle_format=bundle_format,
                            )
                            payload = bundle["payload"]
                            if provenance == "v1":
                                payload["task"]["review_tier"] = 1
                                payload["review_receipts"] = v1_native_payload()["review_receipts"]
                            receipt = payload["review_receipts"][0]
                            # Multibyte text proves these are character, not byte, limits.
                            receipt[field] = "界" * length
                            reseal_review_provenance(payload, receipt)
                            refresh_inner_digests(payload)
                            reseal_evidence_tree(evidence, index, bundle)
                            before = tree_snapshot(evidence)
                            validated = read_evidence_index(evidence)
                            native = next(row for row in validated.entries if row["bundle_state"] == "native")
                            if length <= limit:
                                read_evidence_report(evidence)
                                validate_evidence_source(validated, native)
                            else:
                                self.assert_invalid_readonly(evidence, payload["project_id"])
                                with self.assertRaises(EvidenceConsumerError) as rejected:
                                    validate_evidence_source(validated, native)
                                self.assertEqual(rejected.exception.code, "source_invalid")
                            self.assertEqual(tree_snapshot(evidence), before)

    def test_resealed_acceptance_absence_requires_revision_zero(self):
        for source, bundle_format, index_format in ((19, 1, 1), (29, 2, 2), (31, 4, 3)):
            for revision, acceptance, keep_criterion in (
                (0, "", False), (1, "", False), (1, "   ", False),
                (1, "", True), (1, "   ", True),
            ):
                with self.subTest(source=source, revision=revision, acceptance=acceptance, keep_criterion=keep_criterion), tempfile.TemporaryDirectory() as temporary:
                    evidence, index, bundle = write_versioned_report_tree(
                        Path(temporary), index_source=source, index_format=index_format,
                        bundle_source=source, bundle_format=bundle_format,
                    )
                    read_evidence_report(evidence)
                    payload = bundle["payload"]
                    payload["contract"]["revision"] = revision
                    payload["contract"]["specified"] = revision > 0
                    payload["contract"]["acceptance"] = acceptance
                    if revision == 0:
                        for field in ("scope", "constraints", "authority_ref"):
                            payload["contract"][field] = ""
                    criterion = next(row for row in payload["criteria"] if row["kind"] == "acceptance")
                    criterion["text"] = acceptance
                    if not keep_criterion:
                        payload["criteria"].remove(criterion)
                        payload["criterion_links"] = [
                            row for row in payload["criterion_links"]
                            if row["criterion_id"] != criterion["criterion_id"]
                        ]
                        payload["omissions"].insert(0, "acceptance_criterion_absent")
                    for reference in payload["evidence_references"]:
                        reference["contract_revision"] = revision
                        if not keep_criterion:
                            reference["acceptance_criterion_id"] = None
                    # Rebind every inner digest: rejection must concern the
                    # acceptance rule, not a stale criterion/Reference seal.
                    refresh_inner_digests(payload)
                    reseal_evidence_tree(evidence, index, bundle)
                    before = tree_snapshot(evidence)
                    validated = read_evidence_index(evidence)
                    native = next(row for row in validated.entries if row["bundle_state"] == "native")
                    if revision == 0:
                        read_evidence_report(evidence)
                        validate_evidence_source(validated, native)
                    else:
                        self.assert_invalid_readonly(evidence, payload["project_id"])
                        with self.assertRaises(EvidenceConsumerError) as rejected:
                            validate_evidence_source(validated, native)
                        self.assertEqual(rejected.exception.code, "source_invalid")
                    self.assertEqual(tree_snapshot(evidence), before)

    def test_nonstring_enums_return_fixed_errors_without_writes(self):
        for source, bundle_format, index_format in ((19, 1, 1), (29, 2, 2), (31, 4, 3)):
            for field in ("receipt_kind", "verdict", "criterion_kind"):
                for invalid in ([], {}, None, False, 0):
                    with self.subTest(source=source, field=field, invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                        evidence, index, bundle = write_versioned_report_tree(
                            Path(temporary), index_source=source, index_format=index_format,
                            bundle_source=source, bundle_format=bundle_format,
                        )
                        read_evidence_report(evidence)
                        payload = bundle["payload"]
                        if field == "criterion_kind":
                            # A nonstring kind has no legal criterion digest;
                            # reject its type before hashing or dict lookup.
                            payload["criteria"][0]["kind"] = invalid
                        else:
                            payload["review_receipts"][0][field] = invalid
                            refresh_inner_digests(payload)
                        reseal_evidence_tree(evidence, index, bundle)
                        before = tree_snapshot(evidence)
                        self.assert_invalid_readonly(evidence, payload["project_id"])
                        validated = read_evidence_index(evidence)
                        native = next(row for row in validated.entries if row["bundle_state"] == "native")
                        with self.assertRaises(EvidenceConsumerError) as rejected:
                            validate_evidence_source(validated, native)
                        self.assertEqual(rejected.exception.code, "source_invalid")
                        self.assertEqual(tree_snapshot(evidence), before)

    def test_resealed_nested_hash_keys_return_fixed_errors_without_writes(self):
        paths = (
            ("artifact_manifest", "object_format"),
            ("artifact_manifest", "entries", 0, "kind"),
            ("finding_snapshots", 0, "severity"),
            ("finding_snapshots", 0, "status"),
            ("evidence_references", 0, "source_kind"),
            ("criterion_links", 0, "relation"),
            ("criterion_links", 0, "criterion_id"),
            ("criterion_links", 0, "evidence_reference_id"),
            ("verification_basis", "kind"),
        )
        finding_domain = b"taskgov-completion-bundle-finding-snapshot-v1\0"
        for source, bundle_format, index_format in ((19, 1, 1), (29, 2, 2), (31, 4, 3)):
            for path in paths:
                if source == 19 and path[0] == "verification_basis":
                    continue  # Bundle v1 has no verification-basis object.
                for invalid in ([], {}):
                    with self.subTest(source=source, path=path, invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                        evidence, index, bundle = write_versioned_report_tree(
                            Path(temporary), index_source=source, index_format=index_format,
                            bundle_source=source, bundle_format=bundle_format,
                        )
                        payload = bundle["payload"]
                        payload["artifact_manifest"]["entries"] = [{
                            "ordinal": 0, "kind": "add", "old_path": None,
                            "new_path": "fixture.txt", "before_mode": None,
                            "before_object_id": None, "after_mode": "100644",
                            "after_object_id": "a" * 40,
                        }]
                        finding = {
                            "review_finding_id": "tg_review_finding_aaaaaaaaaaaaaaaa",
                            "review_receipt_id": payload["review_receipts"][0]["review_receipt_id"],
                            "target_generation": payload["target"]["generation"] - 1,
                            "severity": "medium", "summary": "Historical resolved finding.",
                            "status": "resolved", "resolution_summary": "Resolved in retained history.",
                            "created_at": "2026-08-05T00:00:00Z",
                            "resolved_at": "2026-08-05T00:01:00Z",
                            "evidence_reference_id": None, "assurance_class": "legacy_unknown",
                            "producer_class": "legacy_migration", "producer_version": 1,
                        }
                        finding["digest"] = domain_digest(finding_domain, finding)
                        payload["finding_snapshots"] = [finding]
                        payload["omissions"].append("historical_finding_reference_absent")
                        refresh_inner_digests(payload)
                        reseal_evidence_tree(evidence, index, bundle)
                        # Establish validity of the full nested fixture before
                        # introducing exactly one malformed lookup value.
                        read_evidence_report(evidence)
                        validated = read_evidence_index(evidence)
                        native = next(row for row in validated.entries if row["bundle_state"] == "native")
                        validate_evidence_source(validated, native)

                        selected = payload
                        for component in path[:-1]:
                            selected = selected[component]
                        selected[path[-1]] = invalid
                        finding["digest"] = domain_digest(
                            finding_domain, {key: value for key, value in finding.items() if key != "digest"},
                        )
                        if path != ("evidence_references", 0, "source_kind"):
                            refresh_inner_digests(payload)
                        # A nonstring Reference source kind has no valid source
                        # projection; its type must be rejected before lookup.
                        reseal_evidence_tree(evidence, index, bundle)
                        before = tree_snapshot(evidence)
                        self.assert_invalid_readonly(evidence, payload["project_id"])
                        validated = read_evidence_index(evidence)
                        native = next(row for row in validated.entries if row["bundle_state"] == "native")
                        with self.assertRaises(EvidenceConsumerError) as rejected:
                            validate_evidence_source(validated, native)
                        self.assertEqual(rejected.exception.code, "source_invalid")
                        self.assertEqual(tree_snapshot(evidence), before)

    def test_native_continuation_and_collected_provenance_keep_distinct_meaning(self):
        from tests.review_answer_fixture_support import review_fixture
        from tests import evidence_reader_oracle as reader
        from tests import review_continuation_evidence_oracle as graph_reader

        for schema, expected_format, provenance_state in ((29, 3, "v1"), (29, 4, "v1"), (30, 4, "v2")):
            with self.subTest(schema=schema), review_fixture(schema_version=schema) as fixture:
                if schema == 29:
                    artifact, _ = fixture.native_continuation_artifact()
                else:
                    artifact = fixture.collected_source_artifact()
                bundle = deepcopy(artifact.envelope)
                if schema == 29 and expected_format == 4:
                    # Current Bundles also retain legacy reassessment Findings.
                    bundle["format_version"] = 4
                    bundle["payload"].update(bundle_version=4, source_schema_version=31)
                    bundle["payload"]["review_continuation_basis"].update(version=2, collected_captures=[])
                payload = bundle["payload"]
                self.assertEqual(bundle["format_version"], expected_format)
                with tempfile.TemporaryDirectory() as temporary:
                    evidence = Path(temporary) / "evidence"
                    bundles = evidence / "bundles"
                    bundles.mkdir(parents=True)
                    bundle_file = f"bundles/{payload['bundle_id']}.json"
                    evidence.joinpath(*bundle_file.split("/")).write_bytes(artifact.document)
                    index = {
                        "format_version": 2 if expected_format == 3 else 3,
                        "payload": {
                            # Preserve source-30 collected bytes under the current index.
                            "source_schema_version": 29 if expected_format == 3 else 31,
                            "project_id": payload["project_id"],
                            "projection_generation": 1,
                            "bundle_count": 1,
                            "legacy_count": 0,
                            "entries": [{
                                "task_id": payload["task"]["task_id"],
                                "completion_cycle_id": payload["completion_cycle_id"],
                                "cycle_ordinal": payload["cycle_ordinal"],
                                "bundle_state": "native",
                                "bundle_id": payload["bundle_id"],
                                "bundle_file": bundle_file,
                                "bundle_digest": artifact.bundle_digest,
                                "file_digest": artifact.file_digest,
                                "sealed_at": payload["sealed_at"],
                                "bundle_format_version": expected_format,
                            }],
                        },
                    }
                    reseal_evidence_tree(evidence, index, bundle)
                    before = tree_snapshot(evidence)
                    with patch(
                        "task_governance_tool.review_continuation_evidence.validate_graph",
                        side_effect=AssertionError("producer graph validator called"),
                    ), patch(
                        "task_governance_tool.review_answer_provenance.validate_provenance_v2",
                        side_effect=AssertionError("producer provenance validator called"),
                    ):
                        graph = payload["review_continuation_basis"]
                        self.assertEqual(graph_reader.validate_graph(payload, reader), graph)
                        validated = read_evidence_index(evidence)
                        validate_evidence_source(validated, validated.entries[0])
                        report = read_evidence_report(evidence)
                    self.assertEqual(tree_snapshot(evidence), before)
                    projected = report["entries"][0]["review_receipts"]
                    self.assertEqual(len(projected), len(payload["review_receipts"]))
                    self.assertEqual({row["provenance_state"] for row in projected}, {provenance_state})
                    if schema == 30:
                        for receipt in projected:
                            self.assertEqual(set(receipt), {
                                "review_receipt_id", "receipt_kind", "verdict", "provenance_state",
                            })
                        self.assertEqual(report["code_occurrences"], {
                            "review_profiles": {}, "review_lenses": {}, "method_codes": {},
                        })

                    # Exercise every graph-row ID before lookup/hash/regex,
                    # including nullable relations, through both Reader entries.
                    paths = [
                        (collection, position, field)
                        for collection, rows in graph.items() if type(rows) is list
                        for position, row in enumerate(rows) if type(row) is dict
                        for field in row if field.endswith("_id")
                    ]
                    mutations = [(path, value) for path in paths
                                 for value in ([], {}, True, 1)]
                    mutations.extend([
                        (("continuations", 0, "preparation_id"), None),
                        (("references", 0, "source_id"), None),
                        (("references", 0, "source_id"), "tg_review_receipt_" + "f" * 16),
                        (("continuations",), []),
                        (("receipts",), []),
                        (("judgments",), []),
                    ])
                    if graph["findings"]:
                        mutations.extend([(("findings",), []),
                                          (("findings", 0, "review_receipt_id"), None)])
                    for path, invalid in mutations:
                        with self.subTest(schema=schema, graph_path=path, invalid=invalid):
                            mutated = deepcopy(bundle)
                            selected = mutated["payload"]["review_continuation_basis"]
                            for component in path[:-1]:
                                selected = selected[component]
                            selected[path[-1]] = invalid
                            snapshot = canonical_json_bytes(mutated["payload"])
                            with self.assertRaises(EvidenceConsumerError) as rejected:
                                graph_reader.validate_graph(mutated["payload"], reader)
                            self.assertEqual(rejected.exception.code, "source_invalid")
                            self.assertEqual(canonical_json_bytes(mutated["payload"]), snapshot)
                            # Only outer seals change: the malformed graph
                            # field must fail before its assertion is trusted.
                            reseal_evidence_tree(evidence, index, mutated)
                            sealed = tree_snapshot(evidence)
                            validated = read_evidence_index(evidence)
                            with self.assertRaises(EvidenceConsumerError) as rejected:
                                validate_evidence_source(validated, validated.entries[0])
                            self.assertEqual(rejected.exception.code, "source_invalid")
                            self.assert_invalid_readonly(evidence, payload["project_id"])
                            self.assertEqual(tree_snapshot(evidence), sealed)

    def test_current_and_retained_ordinary_formats_keep_report_and_bytes(self):
        for source in range(19, 34):
            bundle_format = 1 if source == 19 else 4 if source >= 30 else 2
            index_format = 1 if source == 19 else 3 if source >= 30 else 2
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                evidence, index, bundle = write_versioned_report_tree(
                    Path(temporary), index_source=source, index_format=index_format,
                    bundle_source=source, bundle_format=bundle_format,
                )
                before = tree_snapshot(evidence)
                report = read_evidence_report(evidence)
                self.assertEqual(tree_snapshot(evidence), before)
                self.assertEqual(report["format_version"], index_format)
                self.assertEqual((report["bundle_count"], report["legacy_count"]), (1, 1))
                self.assertEqual(report["declared_projection_generation"], 7)
                native = next(row for row in report["entries"] if row["bundle_state"] == "native")
                self.assertEqual(native["review_receipts"][0]["provenance_state"], "not_required")
                legacy = next(row for row in report["entries"] if row["bundle_state"] == "legacy_unknown")
                self.assertNotIn("review_receipts", legacy)
                self.assertEqual(report["code_occurrences"], {
                    "review_profiles": {}, "review_lenses": {}, "method_codes": {},
                })
                if source == 19:
                    # Retained independent vector, never regenerated as an expectation.
                    self.assertEqual(bundle["bundle_digest"],
                        "sha256:98d79472e8909bbe01285f52e59b94215023d27783ad08d3491284fad397a992")
                # A new index may point to these exact old Bundle bytes.
                index["format_version"] = 3
                index["payload"]["source_schema_version"] = 33
                for entry in index["payload"]["entries"]:
                    entry["bundle_format_version"] = (
                        bundle_format if entry["bundle_state"] == "native" else None
                    )
                bundle_bytes = tree_snapshot(evidence / "bundles")
                reseal_evidence_tree(evidence, index)
                retained_before = tree_snapshot(evidence)
                retained_report = read_evidence_report(evidence)
                self.assertEqual(retained_report, {**report, "format_version": 3})
                self.assertEqual(tree_snapshot(evidence), retained_before)
                self.assertEqual(tree_snapshot(evidence / "bundles"), bundle_bytes)

    def test_resealed_invalid_source_format_pairs_and_version_ceiling_reject(self):
        cases = (
            # index source/format, Bundle source/format
            (19, 2, 19, 1), (29, 3, 29, 2), (30, 2, 29, 2), (31, 2, 29, 2),
            (34, 3, 31, 4), (30, 3, 31, 4),
            (31, 3, 20, 1), (31, 3, 19, 2), (31, 3, 30, 2), (31, 3, 31, 2),
            (31, 3, 28, 3), (31, 3, 29, 3),  # v3 needs a real continuation.
            (31, 3, 29, 4), (31, 3, 32, 4),
        )
        for index_source, index_format, bundle_source, bundle_format in cases:
            with self.subTest(versions=(index_source, index_format, bundle_source, bundle_format)), tempfile.TemporaryDirectory() as temporary:
                evidence, index, _ = write_versioned_report_tree(
                    Path(temporary), index_source=index_source, index_format=index_format,
                    bundle_source=bundle_source, bundle_format=bundle_format,
                )
                self.assert_invalid_readonly(evidence, index["payload"]["project_id"])

    def test_wrong_domains_reject_even_with_matching_file_and_index_seals(self):
        for source, bundle_format, index_format in ((19, 1, 1), (29, 2, 2), (31, 4, 3)):
            for part in ("index", "bundle"):
                with self.subTest(source=source, part=part), tempfile.TemporaryDirectory() as temporary:
                    evidence, index, bundle = write_versioned_report_tree(
                        Path(temporary), index_source=source, index_format=index_format,
                        bundle_source=source, bundle_format=bundle_format,
                    )
                    read_evidence_report(evidence)  # Establish a valid semantic baseline.
                    if part == "index":
                        wrong_domain = INDEX_DOMAINS[2 if index_format == 1 else 1]
                        reseal_evidence_tree(evidence, index, index_domain=wrong_domain)
                    else:
                        wrong_domain = BUNDLE_DOMAINS[2 if bundle_format == 1 else 1]
                        reseal_evidence_tree(evidence, index, bundle, bundle_domain=wrong_domain)
                    self.assert_invalid_readonly(evidence, index["payload"]["project_id"])

    def test_unsupported_envelopes_and_entry_discriminator_reject(self):
        for part in ("index", "bundle", "entry", "required_root", "project"):
            with self.subTest(part=part), tempfile.TemporaryDirectory() as temporary:
                evidence, index, bundle = write_versioned_report_tree(Path(temporary))
                read_evidence_report(evidence)
                if part == "index":
                    index["format_version"] = 4
                    reseal_evidence_tree(evidence, index, index_domain=b"taskgov-evidence-index-v4\0")
                elif part == "bundle":
                    bundle["format_version"] = 5
                    bundle["payload"]["bundle_version"] = 5
                    next(row for row in index["payload"]["entries"] if row["bundle_state"] == "native")["bundle_format_version"] = 5
                    reseal_evidence_tree(evidence, index, bundle, bundle_domain=b"taskgov-completion-evidence-bundle-v5\0")
                elif part == "entry":
                    next(row for row in index["payload"]["entries"] if row["bundle_state"] == "native")["bundle_format_version"] = 3
                    reseal_evidence_tree(evidence, index)
                elif part == "required_root":
                    del bundle["payload"]["review_continuation_basis"]
                    reseal_evidence_tree(evidence, index, bundle)
                project_id = index["payload"]["project_id"]
                if part == "project":
                    project_id = "tg_project_" + "f" * 32
                self.assert_invalid_readonly(evidence, project_id)


class M224EvidenceAcceptanceTests(unittest.TestCase):
    def test_current_v1_null_bundles_and_report_consumer_are_consistent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = make_physical_install(root / "install")
            setup = require_cli_json(install, "setup")
            project_id = str(setup["project_id"])
            evidence_root = install.fixed_root / "evidence"

            primary_task_id = add_contract_task(
                install,
                title="M22.4 v1 provenance human",
                review_tier=1,
                verification=VERIFICATION_1000,
            )
            primary_generation = set_target(install, primary_task_id, "a")

            human_case = REVIEW_PROVENANCE_V1_CASES[0]
            duplicate_arguments = [
                "review",
                "receipt",
                "add",
                primary_task_id,
                "--reviewer",
                "m224-duplicate-method",
                "--kind",
                "independent",
                "--verdict",
                "pass",
                "--summary",
                "Duplicate method must fail before write.",
                *human_case.cli_options(),
                "--review-method",
                "review_packet_inspection",
                "--json",
            ]
            before_duplicate_db = database_snapshot(install.db_path)
            before_duplicate_tree = tree_snapshot(install.fixed_root)
            duplicate = install.run(*duplicate_arguments)
            duplicate_payload = json_payload(duplicate)
            self.assertEqual(duplicate.returncode, 1, duplicate.stdout)
            self.assertEqual(
                duplicate_payload["errors"][0]["code"],
                "invalid_review_evidence",
            )
            self.assertEqual(database_snapshot(install.db_path), before_duplicate_db)
            self.assertEqual(tree_snapshot(install.fixed_root), before_duplicate_tree)

            verification = require_cli_json(
                install,
                "verification",
                "receipt",
                "add",
                primary_task_id,
                "--result",
                "pass",
                "--duration-ms",
                "11",
                "--scope-coverage",
                "full",
                "--expected-target-generation",
                str(primary_generation),
            )
            verification_receipt_id = str(
                verification["data"]["receipt"]["verification_receipt_id"]
            )

            expected_cases: dict[str, object] = {}
            case_task_receipts: dict[
                str,
                tuple[str, object, dict[str, object]],
            ] = {}
            for case_index, case in enumerate(REVIEW_PROVENANCE_V1_CASES):
                if case_index == 0:
                    case_task_id = primary_task_id
                else:
                    case_task_id = add_contract_task(
                        install,
                        title=f"M22.4 v1 provenance {case.name}",
                        review_tier=1,
                    )
                    set_target(install, case_task_id, format(case_index, "x"))
                receipt = add_case_receipt(
                    install,
                    case_task_id,
                    case,
                    reviewer=f"m224-case-{case.name}",
                )
                receipt_id = str(receipt["review_receipt_id"])
                expected_cases[receipt_id] = case
                expected_provenance = expected_v1_provenance(
                    case,
                    receipt["review_provenance"],
                )
                self.assertEqual(
                    receipt["review_provenance"],
                    expected_provenance,
                )
                case_task_receipts[case_task_id] = (
                    receipt_id,
                    case,
                    expected_provenance,
                )
                completed = complete_task(install, case_task_id)
                self.assertEqual(completed["data"]["task"]["status"], "done")

            repeated_task_id = add_contract_task(
                install,
                title="M22.4 repeated code in another Bundle",
                review_tier=1,
            )
            set_target(install, repeated_task_id, "b")
            repeated_receipt = add_case_receipt(
                install,
                repeated_task_id,
                human_case,
                reviewer="m224-repeated-human",
            )
            repeated_receipt_id = str(repeated_receipt["review_receipt_id"])
            expected_cases[repeated_receipt_id] = human_case
            complete_task(install, repeated_task_id)

            tier2_task_id = add_contract_task(
                install,
                title="M22.4 Tier 2 qualifying basis",
                review_tier=2,
            )
            set_target(install, tier2_task_id, "d")
            tier2_expected_cases: dict[str, object] = {}
            for index, case in enumerate(REVIEW_PROVENANCE_V1_CASES[:2]):
                receipt = add_case_receipt(
                    install,
                    tier2_task_id,
                    case,
                    reviewer=f"m224-tier2-{index}",
                )
                tier2_expected_cases[str(receipt["review_receipt_id"])] = case
            complete_task(install, tier2_task_id)

            fallback_task_ids: dict[int, str] = {}
            fallback_receipts: dict[str, tuple[str, object]] = {}
            for review_tier, marker in ((1, "e"), (2, "f")):
                fallback_task_id = add_contract_task(
                    install,
                    title=f"M22.4 Tier {review_tier} fallback basis",
                    review_tier=review_tier,
                )
                set_target(install, fallback_task_id, marker)
                fallback_receipt = add_fallback_receipt(
                    install,
                    fallback_task_id,
                    human_case,
                    reviewer=f"m224-tier{review_tier}-fallback",
                    user_approved=review_tier == 2,
                )
                fallback_receipts[fallback_task_id] = (
                    str(fallback_receipt["review_receipt_id"]),
                    human_case,
                )
                fallback_task_ids[review_tier] = fallback_task_id
                complete_task(install, fallback_task_id)

            not_required_task_id = add_contract_task(
                install,
                title="M22.4 not-required provenance",
                review_tier=0,
            )
            set_target(install, not_required_task_id, "c")
            not_required_receipt = add_case_receipt(
                install,
                not_required_task_id,
                NOT_REQUIRED_REVIEW_PROVENANCE_CASE,
                reviewer="m224-not-required",
            )
            not_required_receipt_id = str(
                not_required_receipt["review_receipt_id"]
            )
            self.assertIsNone(not_required_receipt["review_provenance"])
            complete_task(
                install,
                not_required_task_id,
            )

            for case_task_id, (
                receipt_id,
                _,
                expected_provenance,
            ) in case_task_receipts.items():
                shown = require_cli_json(install, "task", "show", case_task_id, "--audit")
                shown_receipts = shown["data"]["review_evidence"][
                    "recent_receipts"
                ]
                self.assertEqual(len(shown_receipts), 1)
                self.assertEqual(
                    shown_receipts[0]["review_receipt_id"],
                    receipt_id,
                )
                self.assertEqual(
                    shown_receipts[0]["review_provenance"],
                    expected_provenance,
                )
            repeated_shown = require_cli_json(
                install,
                "task",
                "show",
                repeated_task_id,
                "--audit",
            )
            self.assertEqual(
                repeated_shown["data"]["review_evidence"]["recent_receipts"]
                [0]["review_receipt_id"],
                repeated_receipt_id,
            )
            not_required_shown = require_cli_json(
                install,
                "task",
                "show",
                not_required_task_id,
                "--audit",
            )
            self.assertIsNone(
                not_required_shown["data"]["review_evidence"]["recent_receipts"]
                [0]["review_provenance"]
            )

            with closing(connect_readonly(install.db_path)) as connection:
                provenance_rows = connection.execute(
                    "SELECT receipt.review_receipt_id, "
                    "receipt.review_provenance_basis_version, "
                    "provenance.reviewer_class "
                    "FROM review_receipts AS receipt "
                    "LEFT JOIN review_receipt_provenance AS provenance "
                    "ON provenance.review_receipt_id = receipt.review_receipt_id"
                ).fetchall()
                stored = {
                    str(row["review_receipt_id"]): (
                        int(row["review_provenance_basis_version"]),
                        row["reviewer_class"],
                    )
                    for row in provenance_rows
                }
                all_v1_cases = {
                    **expected_cases,
                    **tier2_expected_cases,
                    **{
                        receipt_id: case
                        for receipt_id, case in fallback_receipts.values()
                    },
                }
                for receipt_id, case in all_v1_cases.items():
                    self.assertEqual(stored[receipt_id], (1, case.reviewer_class))
                self.assertEqual(stored[not_required_receipt_id], (0, None))
                code_counts = {
                    (str(row["code_kind"]), str(row["code"])): int(row["count"])
                    for row in connection.execute(
                        "SELECT code_kind, code, COUNT(*) AS count "
                        "FROM review_receipt_provenance_codes "
                        "GROUP BY code_kind, code"
                    ).fetchall()
                }
            self.assertEqual(
                code_counts,
                {
                    ("profile", "general"): COMMON_CODE_COUNT,
                    ("lens", "correctness"): COMMON_CODE_COUNT,
                    ("method", "review_packet_inspection"): COMMON_CODE_COUNT,
                },
            )

            index, bundles = load_native_bundles(evidence_root)
            expected_task_ids = {
                *case_task_receipts,
                repeated_task_id,
                tier2_task_id,
                *fallback_receipts,
                not_required_task_id,
            }
            self.assertEqual(
                index["payload"]["bundle_count"],
                len(expected_task_ids),
            )
            self.assertEqual(index["payload"]["legacy_count"], 0)
            self.assertEqual(set(bundles), expected_task_ids)

            primary_bundle = bundles[primary_task_id]["payload"]
            self.assertEqual(primary_bundle["task"]["verification"], VERIFICATION_1000)
            self.assertIn(
                VERIFICATION_1000,
                [criterion["text"] for criterion in primary_bundle["criteria"]],
            )
            self.assertEqual(
                primary_bundle["verification_receipt"]["verification_receipt_id"],
                verification_receipt_id,
            )
            subject = primary_bundle["verification_receipt"]["verification_subject"]
            self.assertEqual(subject["basis_version"], 1)
            self.assertNotIn("legacy_caller_label", subject)
            self.assertIsNone(primary_bundle["target"]["base_revision"])
            for case_task_id, (
                receipt_id,
                case,
                expected_provenance,
            ) in case_task_receipts.items():
                case_bundle_receipts = bundles[case_task_id]["payload"][
                    "review_receipts"
                ]
                self.assertEqual(len(case_bundle_receipts), 1)
                selected_receipt = case_bundle_receipts[0]
                self.assertEqual(selected_receipt["review_receipt_id"], receipt_id)
                self.assertEqual(
                    selected_receipt["reviewer_key"],
                    f"m224-case-{case.name}",
                )
                self.assertEqual(selected_receipt["receipt_kind"], "independent")
                self.assertEqual(selected_receipt["verdict"], "pass")
                self.assertEqual(selected_receipt["user_approved"], 0)
                self.assertEqual(
                    selected_receipt["review_provenance"],
                    expected_provenance,
                )
            repeated_bundle_receipts = bundles[repeated_task_id]["payload"][
                "review_receipts"
            ]
            self.assertEqual(
                [receipt["review_receipt_id"] for receipt in repeated_bundle_receipts],
                [repeated_receipt_id],
            )
            for field, expected in human_case.expected_normalized().items():
                self.assertEqual(
                    repeated_bundle_receipts[0]["review_provenance"][field],
                    expected,
                )
            tier2_bundle_receipts = bundles[tier2_task_id]["payload"][
                "review_receipts"
            ]
            self.assertEqual(
                [receipt["review_receipt_id"] for receipt in tier2_bundle_receipts],
                list(tier2_expected_cases),
            )
            for receipt in tier2_bundle_receipts:
                case = tier2_expected_cases[str(receipt["review_receipt_id"])]
                for field, expected in case.expected_normalized().items():
                    self.assertEqual(receipt["review_provenance"][field], expected)
            for fallback_task_id, (fallback_receipt_id, case) in (
                fallback_receipts.items()
            ):
                fallback_bundle_receipts = bundles[fallback_task_id]["payload"][
                    "review_receipts"
                ]
                self.assertEqual(
                    [
                        receipt["review_receipt_id"]
                        for receipt in fallback_bundle_receipts
                    ],
                    [fallback_receipt_id],
                )
                self.assertEqual(
                    fallback_bundle_receipts[0]["receipt_kind"],
                    "self_review_fallback",
                )
                self.assertEqual(
                    fallback_bundle_receipts[0]["user_approved"],
                    int(
                        bundles[fallback_task_id]["payload"]["task"][
                            "review_tier"
                        ]
                        == 2
                    ),
                )
                for field, expected in case.expected_normalized().items():
                    self.assertEqual(
                        fallback_bundle_receipts[0]["review_provenance"][field],
                        expected,
                    )
            self.assertIsNone(
                bundles[not_required_task_id]["payload"]["review_receipts"][0][
                    "review_provenance"
                ]
            )

            report_copy = root / "evidence-report-copy"
            shutil.copytree(evidence_root, report_copy)
            before_report = tree_snapshot(report_copy)
            report = read_evidence_report(
                report_copy,
                expected_project_id=project_id,
            )
            self.assertEqual(tree_snapshot(report_copy), before_report)
            self.assertEqual(index["format_version"], 3)
            self.assertEqual(index["payload"]["source_schema_version"], 34)
            self.assertEqual(primary_bundle["source_schema_version"], 34)
            self.assertEqual(primary_bundle["bundle_version"], 4)
            self.assertEqual(bundles[primary_task_id]["format_version"], 4)
            self.assertIsNone(primary_bundle["review_continuation_basis"])
            # Re-seal unsupported source versions so rejection cannot depend
            # merely on a stale digest after this reader's schema-34 admission.
            for changed_part in ("index", "bundle"):
                with self.subTest(future_source=changed_part):
                    future_root = root / f"evidence-future-{changed_part}"
                    shutil.copytree(report_copy, future_root)
                    future_index = deepcopy(index)
                    if changed_part == "index":
                        future_index["payload"]["source_schema_version"] = 35
                        reseal_evidence_tree(future_root, future_index)
                    else:
                        future_bundle = deepcopy(bundles[primary_task_id])
                        future_bundle["payload"]["source_schema_version"] = 35
                        reseal_evidence_tree(future_root, future_index, future_bundle)
                    future_before = tree_snapshot(future_root)
                    with self.assertRaises(EvidenceReportError) as rejected:
                        read_evidence_report(future_root, expected_project_id=project_id)
                    self.assertEqual(rejected.exception.code, "evidence_report_invalid")
                    self.assertEqual(tree_snapshot(future_root), future_before)
            self.assertEqual(
                (report["bundle_count"], report["legacy_count"]),
                (len(expected_task_ids), 0),
            )
            self.assertEqual(
                report["code_occurrences"],
                {
                    "review_profiles": {"general": PROJECTED_CODE_COUNT},
                    "review_lenses": {"correctness": PROJECTED_CODE_COUNT},
                    "method_codes": {
                        "review_packet_inspection": PROJECTED_CODE_COUNT
                    },
                },
            )
            report_entries = {
                str(entry["task_id"]): entry for entry in report["entries"]
            }
            self.assertEqual(set(report_entries), expected_task_ids)
            for case_task_id, (receipt_id, case, _) in (
                case_task_receipts.items()
            ):
                self.assertEqual(
                    report_entries[case_task_id]["review_receipts"],
                    [expected_report_receipt(receipt_id, case)],
                )
            self.assertEqual(
                report_entries[repeated_task_id]["review_receipts"],
                [expected_report_receipt(repeated_receipt_id, human_case)],
            )
            self.assertEqual(
                len(report_entries[tier2_task_id]["review_receipts"]),
                2,
            )
            self.assertTrue(
                all(
                    receipt["provenance_state"] == "v1"
                    for receipt in report_entries[tier2_task_id]["review_receipts"]
                )
            )
            for fallback_task_id, (fallback_receipt_id, _) in (
                fallback_receipts.items()
            ):
                projected_receipts = report_entries[fallback_task_id][
                    "review_receipts"
                ]
                self.assertEqual(len(projected_receipts), 1)
                self.assertEqual(
                    projected_receipts[0]["review_receipt_id"],
                    fallback_receipt_id,
                )
                self.assertEqual(
                    projected_receipts[0]["receipt_kind"],
                    "self_review_fallback",
                )
                self.assertEqual(projected_receipts[0]["verdict"], "pass")
                self.assertEqual(
                    projected_receipts[0]["provenance_state"],
                    "v1",
                )
            self.assertEqual(
                report_entries[not_required_task_id]["review_receipts"][0][
                    "provenance_state"
                ],
                "not_required",
            )

            invalid_basis_cases = (
                ("tier0_empty", not_required_task_id, "empty"),
                ("tier0_excess", not_required_task_id, "excess"),
                ("tier1_empty", primary_task_id, "empty"),
                ("tier1_changes_requested", primary_task_id, "changes_requested"),
                ("tier1_excess", primary_task_id, "excess"),
                ("tier2_empty", tier2_task_id, "empty"),
                ("tier2_single", tier2_task_id, "single"),
                ("tier2_excess", tier2_task_id, "excess"),
                ("tier2_duplicate_reviewer", tier2_task_id, "duplicate_reviewer"),
                ("tier2_reversed", tier2_task_id, "reversed"),
                ("tier2_mixed_fallback", tier2_task_id, "mixed_fallback"),
                (
                    "tier1_approved_fallback",
                    fallback_task_ids[1],
                    "tier1_approved_fallback",
                ),
                (
                    "tier2_unapproved_fallback",
                    fallback_task_ids[2],
                    "tier2_unapproved_fallback",
                ),
            )
            for name, task_id, mutation in invalid_basis_cases:
                with self.subTest(invalid_gate_basis=name):
                    invalid_root = root / f"evidence-invalid-{name}"
                    shutil.copytree(evidence_root, invalid_root)
                    rewrite_gate_basis(
                        invalid_root,
                        task_id=task_id,
                        mutation=mutation,
                    )
                    invalid_before = tree_snapshot(invalid_root)
                    with self.assertRaises(EvidenceReportError) as report_error:
                        read_evidence_report(
                            invalid_root,
                            expected_project_id=project_id,
                        )
                    self.assertEqual(
                        report_error.exception.code,
                        "evidence_report_invalid",
                    )
                    self.assertEqual(tree_snapshot(invalid_root), invalid_before)

            referenced_bundle = next(
                report_copy.joinpath(*entry["bundle_file"].split("/"))
                for entry in index["payload"]["entries"]
                if entry["bundle_state"] == "native"
            )
            orphan = (
                report_copy
                / "bundles"
                / "tg_completion_evidence_bundle_deadbeefdeadbeef.json"
            )
            shutil.copy2(referenced_bundle, orphan)
            orphan_tree = tree_snapshot(report_copy)
            self.assertEqual(
                read_evidence_report(report_copy, expected_project_id=project_id),
                report,
            )
            self.assertEqual(tree_snapshot(report_copy), orphan_tree)

            tampered = root / "evidence-report-tampered"
            shutil.copytree(report_copy, tampered)
            tampered_bundle = tampered / "bundles" / referenced_bundle.name
            tampered_bundle.write_bytes(tampered_bundle.read_bytes() + b" ")
            tampered_tree = tree_snapshot(tampered)
            with self.assertRaises(EvidenceReportError) as report_error:
                read_evidence_report(tampered, expected_project_id=project_id)
            self.assertEqual(report_error.exception.code, "evidence_report_invalid")
            self.assertEqual(tree_snapshot(tampered), tampered_tree)

            pointer_task_id = add_contract_task(
                install,
                title="M22.4 dangling Contract projection canary",
                review_tier=1,
            )
            canonical_before = tree_snapshot(install.fixed_root)
            for state, task_id in (
                ("completed", primary_task_id),
                ("active", pointer_task_id),
            ):
                with self.subTest(contract_pointer_state=state):
                    corrupt_db = root / f"dangling-{state}-contract-copy.sqlite"
                    shutil.copy2(install.db_path, corrupt_db)
                    inject_contract_pointer_fault(corrupt_db, task_id, pointer=99)
                    corrupt_before = database_snapshot(corrupt_db)
                    with closing(connect_readonly(corrupt_db)) as connection:
                        with self.assertRaises(StorageError) as storage_error:
                            capture_evidence_projection_basis(
                                connection,
                                project_id=project_id,
                            )
                    self.assertEqual(
                        storage_error.exception.code,
                        "evidence_ledger_inconsistent",
                    )
                    self.assertEqual(
                        storage_error.exception.message,
                        "stored evidence ledger is inconsistent",
                    )
                    self.assertEqual(
                        database_snapshot(corrupt_db),
                        corrupt_before,
                    )
            self.assertEqual(tree_snapshot(install.fixed_root), canonical_before)


if __name__ == "__main__":
    unittest.main()
