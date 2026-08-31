from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tests.m14_test_support import tree_snapshot
from tests.m23_test_support import (
    domain_digest,
    expected_markdown_v1,
    held_analysis_tree_snapshot as _held_tree_snapshot,
    reference_json_bytes,
    write_mixed_evidence_tree,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = ROOT / "task-governance-tool" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from task_governance_tool._analysis_windows_process import (  # noqa: E402
    MockScenario,
)
from task_governance_tool.analysis_contracts import default_recipe  # noqa: E402
from task_governance_tool.analysis_outbox import (  # noqa: E402
    enqueue_analysis_source,
    replace_analysis_status,
)
from task_governance_tool.analysis_packet import (  # noqa: E402
    FIXED_PROMPT_DIGEST,
    build_analysis_packet,
)
from task_governance_tool.analysis_validator import build_analysis_report  # noqa: E402
from task_governance_tool.analysis_worker import (  # noqa: E402
    AnalysisWorkerError,
    run_once,
)
from task_governance_tool.codex_analysis_adapter import (  # noqa: E402
    ClosedMockPlan,
)
from task_governance_tool.evidence_consumer import (  # noqa: E402
    read_evidence_index,
    validate_evidence_source,
)
from task_governance_tool.state_paths import analysis_state_paths  # noqa: E402


REPORT_DOMAIN = b"taskgov-analysis-report-v1\0"
CITATION_DOMAIN = b"taskgov-analysis-citation-v1\0"
BUNDLE_DOMAIN = b"taskgov-completion-evidence-bundle-v1\0"
REVIEW_PROVENANCE_DOMAIN = b"taskgov-review-provenance-v1\0"
SOURCE_DOMAIN = b"taskgov-analysis-source-v1\0"
RECIPE_DOMAIN = b"taskgov-analysis-recipe-v1\0"
JOB_DOMAIN = b"taskgov-analysis-job-v1\0"
DESCRIPTOR_DOMAIN = b"taskgov-analysis-descriptor-v1\0"
PACKET_DOMAIN = b"taskgov-analysis-packet-v1\0"
REPORT_ID_DOMAIN = b"taskgov-analysis-report-id-v1\0"


def _entry_identity(entry: dict[str, object]) -> tuple[str, str, int]:
    return (
        str(entry["task_id"]),
        str(entry["completion_cycle_id"]),
        int(entry["cycle_ordinal"]),
    )


def _resolve_pointer(document: object, pointer: str) -> object:
    if not pointer.startswith("/"):
        raise AssertionError("fixture pointer is not absolute")
    current = document
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise AssertionError("fixture pointer does not resolve")
    return current


@unittest.skipUnless(os.name == "nt", "TG-M23.3 integration is Windows-only")
class AnalysisIntegrationTests(unittest.TestCase):
    def _sentinels(self, fixed: Path) -> dict[Path, bytes]:
        fixed.mkdir()
        values = {
            fixed / "taskgov.sqlite": b"sqlite-owner-sentinel",
            fixed / "task-gate-sentinel.json": b'{"gate":"unchanged"}\n',
        }
        for path, value in values.items():
            path.write_bytes(value)
        return values

    def _assert_sentinels_unchanged(self, values: dict[Path, bytes]) -> None:
        for path, value in values.items():
            self.assertEqual(path.read_bytes(), value)
        database = next(path for path in values if path.name == "taskgov.sqlite")
        for suffix in ("-journal", "-wal", "-shm"):
            self.assertFalse(Path(str(database) + suffix).exists())

    def _assert_clean_analysis_tree(
        self,
        paths,
        *,
        job_ids: set[str],
        report_ids: set[str],
    ) -> None:
        snapshot = _held_tree_snapshot(paths)
        self.assertEqual(
            {row[1] for row in snapshot if len(row) == 5},
            {
                paths.lock.name,
                paths.outbox.name,
                paths.status.name,
                paths.reports.name,
                paths.rendered.name,
                paths.temporary.name,
            },
        )
        children = [row for row in snapshot if len(row) == 7]
        self.assertFalse([row for row in children if row[4] or row[5]])
        expected = {
            paths.outbox.name: {f"{job_id}.json" for job_id in job_ids},
            paths.status.name: {f"{job_id}.json" for job_id in job_ids},
            paths.reports.name: {f"{report_id}.json" for report_id in report_ids},
            paths.rendered.name: {f"{report_id}.md" for report_id in report_ids},
            paths.temporary.name: set(),
        }
        for directory_name, names in expected.items():
            self.assertEqual(
                {row[1] for row in children if row[0] == directory_name},
                names,
            )

    def _report_payload(
        self,
        paths,
        status: dict[str, object],
        *,
        descriptor: dict[str, object],
        expected_recipe: dict[str, object],
        source,
    ) -> dict[str, object]:
        report_id = str(status["report_id"])
        snapshot = _held_tree_snapshot(paths)

        def content(directory_name: str, basename: str) -> bytes:
            matching = [
                row[6]
                for row in snapshot
                if len(row) == 7
                and row[0] == directory_name
                and row[1] == basename
                and not row[4]
            ]
            self.assertEqual(len(matching), 1)
            return matching[0]

        document = content(paths.reports.name, f"{report_id}.json")
        envelope = json.loads(document)
        self.assertEqual(
            set(envelope),
            {"report_schema_version", "report_digest", "payload"},
        )
        self.assertEqual(envelope["report_schema_version"], 1)
        self.assertEqual(document, reference_json_bytes(envelope) + b"\n")
        payload = envelope["payload"]
        self.assertEqual(
            set(payload),
            {
                "report_id",
                "analysis_job_id",
                "source_kind",
                "source_key",
                "recipe_digest",
                "inference_state",
                "structural_facts",
                "trusted_caller_declarations",
                "legacy_absence",
                "llm_derived",
                "omissions",
                "uncertainties",
                "declared_code_occurrences",
                "citations",
                "reproducibility",
            },
        )
        self.assertEqual(
            envelope["report_digest"],
            domain_digest(REPORT_DOMAIN, payload),
        )
        self.assertEqual(status["report_digest"], envelope["report_digest"])

        entry = source.source_basis["entry"]
        if source.source_kind == "native_bundle":
            identity = {
                "project_id": source.source_basis["project_id"],
                "task_id": entry["task_id"],
                "completion_cycle_id": entry["completion_cycle_id"],
                "cycle_ordinal": entry["cycle_ordinal"],
                "bundle_id": entry["bundle_id"],
                "bundle_digest": entry["bundle_digest"],
                "file_digest": entry["file_digest"],
            }
        else:
            identity = {
                "project_id": source.source_basis["project_id"],
                "task_id": entry["task_id"],
                "completion_cycle_id": entry["completion_cycle_id"],
                "cycle_ordinal": entry["cycle_ordinal"],
                "bundle_state": "legacy_unknown",
            }
        expected_source_key = domain_digest(SOURCE_DOMAIN, identity)
        self.assertEqual(descriptor["recipe"], expected_recipe)
        expected_recipe_digest = domain_digest(RECIPE_DOMAIN, expected_recipe)
        expected_job_id = "tg_analysis_job_" + hashlib.sha256(
            JOB_DOMAIN
            + expected_source_key.encode("ascii")
            + b"\0"
            + expected_recipe_digest.encode("ascii")
        ).hexdigest()[:16]
        descriptor_body = dict(descriptor)
        descriptor_body.pop("descriptor_digest")
        self.assertEqual(descriptor["descriptor_version"], 1)
        self.assertEqual(descriptor["source_key"], expected_source_key)
        self.assertEqual(descriptor["recipe_digest"], expected_recipe_digest)
        self.assertEqual(descriptor["analysis_job_id"], expected_job_id)
        self.assertEqual(
            descriptor["descriptor_digest"],
            domain_digest(DESCRIPTOR_DOMAIN, descriptor_body),
        )
        packet = {
            "packet_version": 1,
            "analysis_job_id": expected_job_id,
            "source_kind": source.source_kind,
            "source_basis": source.source_basis,
            "source": source.source,
        }
        expected_input_digest = domain_digest(PACKET_DOMAIN, packet)
        accepted_basis = status["accepted_output_digest"] or "offline-null"
        expected_report_id = "tg_analysis_report_" + hashlib.sha256(
            REPORT_ID_DOMAIN
            + expected_source_key.encode("ascii")
            + b"\0"
            + expected_recipe_digest.encode("ascii")
            + b"\0"
            + str(status["inference_state"]).encode("ascii")
            + b"\0"
            + str(accepted_basis).encode("ascii")
        ).hexdigest()[:16]
        self.assertEqual(status["packet_digest"], expected_input_digest)
        self.assertEqual(report_id, expected_report_id)
        self.assertEqual(payload["report_id"], expected_report_id)
        self.assertEqual(payload["analysis_job_id"], expected_job_id)
        self.assertEqual(payload["source_kind"], source.source_kind)
        self.assertEqual(payload["source_key"], expected_source_key)
        self.assertEqual(payload["recipe_digest"], expected_recipe_digest)
        self.assertEqual(payload["inference_state"], status["inference_state"])
        recipe = expected_recipe
        self.assertEqual(
            payload["reproducibility"],
            {
                "producer_version": recipe["producer_version"],
                "declared_model_id": recipe["declared_model_id"],
                "prompt_schema_version": recipe["prompt_schema_version"],
                "prompt_digest": (
                    None
                    if recipe["inference_mode"] == "offline"
                    else FIXED_PROMPT_DIGEST
                ),
                "input_digest": expected_input_digest,
                "accepted_output_digest": status["accepted_output_digest"],
                "report_schema_version": recipe["report_schema_version"],
                "renderer_version": recipe["renderer_version"],
            },
        )
        rendered = content(paths.rendered.name, f"{report_id}.md")
        self.assertEqual(rendered, expected_markdown_v1(envelope))
        self.assertEqual(
            status["render_digest"],
            "sha256:" + hashlib.sha256(rendered).hexdigest(),
        )
        return payload

    def _assert_native_citations(
        self,
        report: dict[str, object],
        source,
    ) -> None:
        entry = source.source_basis["entry"]
        for citation in report["citations"]:
            without_id = dict(citation)
            citation_id = without_id.pop("citation_id")
            expected_id = "tg_analysis_citation_" + hashlib.sha256(
                CITATION_DOMAIN + reference_json_bytes(without_id)
            ).hexdigest()[:16]
            self.assertEqual(citation_id, expected_id)
            self.assertEqual(citation["source_key"], report["source_key"])
            self.assertEqual(citation["bundle_id"], entry["bundle_id"])
            self.assertEqual(citation["bundle_digest"], entry["bundle_digest"])
            self.assertEqual(citation["file_digest"], entry["file_digest"])
            _resolve_pointer(source.source, citation["json_pointer"])

    def test_offline_mixed_queue_drains_in_job_order_and_preserves_traces(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = write_mixed_evidence_tree(root / "source")
            evidence_before = tree_snapshot(evidence)
            fixed = root / "fixed"
            sentinels = self._sentinels(fixed)
            paths = analysis_state_paths(fixed)
            index = read_evidence_index(evidence)
            records: dict[str, dict[str, object]] = {}
            for entry in reversed(index.entries):
                source = validate_evidence_source(index, entry)
                queued = enqueue_analysis_source(
                    paths=paths,
                    source=source,
                    recipe=default_recipe(),
                )
                records[queued.descriptor["analysis_job_id"]] = {
                    "entry": entry,
                    "source": source,
                    "queued": queued,
                }

            observed: dict[str, object] = {}
            expected_order = sorted(records)
            for expected_job_id in expected_order:
                restarted_paths = analysis_state_paths(fixed)
                restarted_index = read_evidence_index(evidence)
                result = run_once(restarted_paths, restarted_index)
                self.assertEqual(result.disposition, "published")
                self.assertEqual(result.analysis_job_id, expected_job_id)
                observed[expected_job_id] = result
            self.assertEqual(
                run_once(analysis_state_paths(fixed), read_evidence_index(evidence)).disposition,
                "idle",
            )

            reports: dict[tuple[str, str, int], dict[str, object]] = {}
            for job_id, record in records.items():
                result = observed[job_id]
                status = result.status
                self.assertEqual(status["state"], "published")
                self.assertEqual(status["worker_attempt_count"], 1)
                self.assertEqual(status["adapter_attempt_count"], 0)
                self.assertEqual(status["inference_state"], "disabled")
                report = self._report_payload(
                    paths,
                    status,
                    descriptor=record["queued"].descriptor,
                    expected_recipe=default_recipe(),
                    source=record["source"],
                )
                entry = record["entry"]
                reports[_entry_identity(entry)] = report
                self.assertEqual(report["analysis_job_id"], job_id)
                self.assertEqual(report["source_kind"], record["source"].source_kind)
                if entry["bundle_state"] == "native":
                    self._assert_native_citations(report, record["source"])
                else:
                    self.assertEqual(report["structural_facts"], [])
                    self.assertEqual(report["trusted_caller_declarations"], [])
                    self.assertEqual(report["declared_code_occurrences"], [])
                    self.assertEqual(len(report["citations"]), 1)
                    citation = report["citations"][0]
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
                    self.assertEqual(citation["citation_kind"], "legacy_index_entry")
                    self.assertEqual(citation["task_id"], entry["task_id"])
                    without_id = dict(citation)
                    citation_id = without_id.pop("citation_id")
                    self.assertEqual(
                        citation_id,
                        "tg_analysis_citation_"
                        + hashlib.sha256(
                            CITATION_DOMAIN + reference_json_bytes(without_id)
                        ).hexdigest()[:16],
                    )
                    basis = record["source"].source_basis
                    self.assertEqual(citation["source_key"], report["source_key"])
                    self.assertEqual(citation["project_id"], basis["project_id"])
                    self.assertEqual(
                        citation["projection_generation"],
                        basis["projection_generation"],
                    )
                    self.assertEqual(citation["index_digest"], basis["index_digest"])
                    self.assertEqual(
                        citation["completion_cycle_id"],
                        entry["completion_cycle_id"],
                    )
                    self.assertEqual(citation["cycle_ordinal"], entry["cycle_ordinal"])
                    self.assertEqual(
                        report["legacy_absence"],
                        {
                            "state": "legacy_unknown",
                            "receipt_detail": "unavailable",
                            "provenance_detail": "unavailable",
                            "citation_id": citation["citation_id"],
                        },
                    )

            native_reports = {
                identity[2]: report
                for identity, report in reports.items()
                if report["source_kind"] == "native_bundle"
            }
            human = native_reports[2]
            llm = native_reports[3]
            not_required = native_reports[4]
            human_values = {
                (item["declaration_kind"], json.dumps(item["value"], sort_keys=True))
                for item in human["trusted_caller_declarations"]
            }
            self.assertEqual(
                human_values,
                {
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
                },
            )
            llm_values = {
                item["declaration_kind"]: item["value"]
                for item in llm["trusted_caller_declarations"]
                if item["declaration_kind"] not in {"profile", "lens", "method"}
            }
            self.assertEqual(
                llm_values,
                {
                    "reviewer_class": "llm",
                    "model_state": "declared",
                    "declared_model_id": "fixture-model-b",
                    "skill_state": "declared",
                    "declared_skill_id": "fixture-skill-b",
                    "declared_skill_version": "1.0",
                    "context_relation": "forked_context",
                },
            )
            self.assertEqual(not_required["trusted_caller_declarations"], [])
            self.assertEqual(not_required["declared_code_occurrences"], [])
            self.assertEqual(
                len(
                    [
                        item
                        for item in not_required["structural_facts"]
                        if item["fact_kind"] == "review_provenance"
                        and item["value"] is None
                    ]
                ),
                1,
            )

            repeated: dict[str, list[tuple[str, str, str]]] = {}
            expected_occurrences = {
                "profile": (
                    "general",
                    "/payload/review_receipts/0/review_provenance/review_profiles/0",
                ),
                "lens": (
                    "correctness",
                    "/payload/review_receipts/0/review_provenance/review_lenses/0",
                ),
                "method": (
                    "review_packet_inspection",
                    "/payload/review_receipts/0/review_provenance/method_codes/0",
                ),
            }
            native_records = {
                int(record["entry"]["cycle_ordinal"]): record
                for record in records.values()
                if record["entry"]["bundle_state"] == "native"
            }
            for ordinal, suffix in ((2, "a"), (3, "b")):
                report = native_reports[ordinal]
                record = native_records[ordinal]
                source_document = record["source"].source
                provenance = source_document["payload"]["review_receipts"][0][
                    "review_provenance"
                ]
                receipt = source_document["payload"]["review_receipts"][0]
                bundle_id = "tg_completion_evidence_bundle_" + suffix * 16
                receipt_id = "tg_review_receipt_" + suffix * 16
                provenance_id = "tg_review_provenance_" + suffix * 16
                self.assertEqual(source_document["format_version"], 1)
                self.assertEqual(record["entry"]["bundle_id"], bundle_id)
                self.assertEqual(
                    record["entry"]["bundle_digest"],
                    domain_digest(BUNDLE_DOMAIN, source_document["payload"]),
                )
                self.assertEqual(
                    record["entry"]["file_digest"],
                    "sha256:"
                    + hashlib.sha256(
                        reference_json_bytes(source_document) + b"\n"
                    ).hexdigest(),
                )
                self.assertEqual(
                    receipt["review_receipt_id"],
                    receipt_id,
                )
                self.assertEqual(provenance["review_provenance_id"], provenance_id)
                self.assertEqual(provenance["provenance_version"], 1)
                target = deepcopy(source_document["payload"]["target"])
                target["base_revision"] = target["base_revision"] or ""
                expected_provenance = {
                    "project_id": source_document["payload"]["project_id"],
                    "task_id": source_document["payload"]["task"]["task_id"],
                    "review_receipt_id": receipt_id,
                    "receipt_kind": receipt["receipt_kind"],
                    "target": target,
                    **{
                        key: provenance[key]
                        for key in (
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
                    },
                }
                self.assertEqual(
                    provenance["digest"],
                    domain_digest(REVIEW_PROVENANCE_DOMAIN, expected_provenance),
                )
                citations = {item["citation_id"]: item for item in report["citations"]}
                occurrences = {
                    item["kind"]: item
                    for item in report["declared_code_occurrences"]
                }
                self.assertEqual(set(occurrences), set(expected_occurrences))
                for kind, (code, pointer) in expected_occurrences.items():
                    occurrence = occurrences[kind]
                    self.assertEqual(occurrence["code"], code)
                    self.assertEqual(occurrence["bundle_id"], bundle_id)
                    self.assertEqual(occurrence["review_receipt_id"], receipt_id)
                    self.assertEqual(
                        occurrence["review_provenance_id"], provenance_id
                    )
                    self.assertEqual(len(occurrence["citation_ids"]), 1)
                    citation = citations[occurrence["citation_ids"][0]]
                    self.assertEqual(citation["citation_kind"], "review_provenance")
                    self.assertEqual(citation["json_pointer"], pointer)
                    self.assertEqual(_resolve_pointer(source_document, pointer), code)
                    self.assertEqual(citation["entity_id"], provenance_id)
                    self.assertEqual(citation["entity_digest"], provenance["digest"])
                    repeated.setdefault(occurrence["code"], []).append(
                        (bundle_id, receipt_id, provenance_id)
                    )
            self.assertEqual(
                set(repeated),
                {"general", "correctness", "review_packet_inspection"},
            )
            for occurrences in repeated.values():
                self.assertEqual(len(occurrences), 2)
                self.assertEqual(len(set(occurrences)), 2)

            before_replay = _held_tree_snapshot(paths)
            restarted_index = read_evidence_index(evidence)
            for entry in reversed(restarted_index.entries):
                replayed = enqueue_analysis_source(
                    paths=analysis_state_paths(fixed),
                    source=validate_evidence_source(restarted_index, entry),
                    recipe=default_recipe(),
                )
                self.assertTrue(replayed.replayed)
                self.assertEqual(replayed.status["state"], "published")
            self.assertEqual(_held_tree_snapshot(paths), before_replay)
            self.assertEqual(tree_snapshot(evidence), evidence_before)
            self._assert_sentinels_unchanged(sentinels)
            self._assert_clean_analysis_tree(
                paths,
                job_ids=set(records),
                report_ids={
                    str(result.status["report_id"]) for result in observed.values()
                },
            )

    def test_restart_reclaims_one_job_without_blocking_the_remaining_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = write_mixed_evidence_tree(root / "source")
            evidence_before = tree_snapshot(evidence)
            fixed = root / "fixed"
            sentinels = self._sentinels(fixed)
            paths = analysis_state_paths(fixed)
            index = read_evidence_index(evidence)
            optional_recipe = default_recipe(
                inference_mode="codex_optional",
                declared_model_id="fixture-model",
            )
            records = {}
            for entry in index.entries[:3]:
                source = validate_evidence_source(index, entry)
                queued = enqueue_analysis_source(
                    paths=paths,
                    source=source,
                    recipe=optional_recipe,
                )
                records[queued.descriptor["analysis_job_id"]] = (source, queued)

            before_invalid_plan = _held_tree_snapshot(paths)
            with self.assertRaises(AnalysisWorkerError):
                run_once(paths, index, object())
            self.assertEqual(_held_tree_snapshot(paths), before_invalid_plan)

            first_job_id, recovery_job_id, remaining_job_id = sorted(records)
            first_source, first = records[first_job_id]
            packet = build_analysis_packet(first.descriptor, first_source)
            stale_running = deepcopy(first.status)
            stale_running.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": packet.packet_digest,
                }
            )
            replace_analysis_status(
                paths=paths,
                descriptor=first.descriptor,
                expected_status=first.status,
                status=stale_running,
            )

            recovery_source, recovery = records[recovery_job_id]
            recovery_packet = build_analysis_packet(
                recovery.descriptor,
                recovery_source,
            )
            recovery_running = deepcopy(recovery.status)
            recovery_running.update(
                {
                    "state": "running",
                    "worker_attempt_count": 1,
                    "packet_digest": recovery_packet.packet_digest,
                }
            )
            recovery_running = replace_analysis_status(
                paths=paths,
                descriptor=recovery.descriptor,
                expected_status=recovery.status,
                status=recovery_running,
            )
            recovery_ready = deepcopy(recovery_running)
            recovery_ready["inference_state"] = "policy_blocked"
            recovery_ready = replace_analysis_status(
                paths=paths,
                descriptor=recovery.descriptor,
                expected_status=recovery_running,
                status=recovery_ready,
            )
            recovery_report = build_analysis_report(
                descriptor=recovery.descriptor,
                packet=recovery_packet,
                inference_state="policy_blocked",
                prompt_digest=FIXED_PROMPT_DIGEST,
            )
            recovery_intent = deepcopy(recovery_ready)
            recovery_intent.update(
                {
                    "report_id": recovery_report.report_id,
                    "report_digest": recovery_report.report_digest,
                    "render_digest": recovery_report.render_digest,
                }
            )
            replace_analysis_status(
                paths=paths,
                descriptor=recovery.descriptor,
                expected_status=recovery_ready,
                status=recovery_intent,
            )
            (paths.reports / f"{recovery_report.report_id}.json").write_bytes(
                recovery_report.report_document
            )
            (paths.rendered / f"{recovery_report.report_id}.md").write_bytes(
                recovery_report.markdown_bytes
            )

            plan = ClosedMockPlan((MockScenario.SUCCESS,))
            reclaimed = run_once(
                analysis_state_paths(fixed),
                read_evidence_index(evidence),
                plan,
            )
            self.assertEqual(reclaimed.analysis_job_id, first_job_id)
            self.assertEqual(reclaimed.disposition, "failed")
            self.assertEqual(reclaimed.status["fixed_code"], "interrupted")
            self.assertEqual(reclaimed.status["worker_attempt_count"], 2)
            self.assertIsNone(reclaimed.status["report_id"])

            recovered = run_once(
                analysis_state_paths(fixed),
                read_evidence_index(evidence),
                plan,
            )
            self.assertEqual(recovered.analysis_job_id, recovery_job_id)
            self.assertEqual(recovered.disposition, "published")
            self.assertEqual(recovered.status["worker_attempt_count"], 1)
            self.assertEqual(recovered.status["adapter_attempt_count"], 0)

            remaining = run_once(
                analysis_state_paths(fixed),
                read_evidence_index(evidence),
                plan,
            )
            self.assertEqual(remaining.analysis_job_id, remaining_job_id)
            self.assertEqual(remaining.disposition, "published")
            self.assertEqual(remaining.status["inference_state"], "succeeded")
            self.assertEqual(
                run_once(analysis_state_paths(fixed), read_evidence_index(evidence)).disposition,
                "idle",
            )

            before_replay = _held_tree_snapshot(paths)
            restarted_index = read_evidence_index(evidence)
            for entry in restarted_index.entries[:3]:
                replayed = enqueue_analysis_source(
                    paths=analysis_state_paths(fixed),
                    source=validate_evidence_source(restarted_index, entry),
                    recipe=optional_recipe,
                )
                self.assertTrue(replayed.replayed)
                self.assertIn(replayed.status["state"], {"failed", "published"})
            self.assertEqual(_held_tree_snapshot(paths), before_replay)
            self.assertEqual(tree_snapshot(evidence), evidence_before)
            self._assert_sentinels_unchanged(sentinels)
            self._assert_clean_analysis_tree(
                paths,
                job_ids=set(records),
                report_ids={
                    str(recovered.status["report_id"]),
                    str(remaining.status["report_id"]),
                },
            )

    def test_mock_retry_and_policy_blocked_are_bounded_and_leave_no_raw_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = write_mixed_evidence_tree(root / "source")
            evidence_before = tree_snapshot(evidence)
            fixed = root / "fixed"
            sentinels = self._sentinels(fixed)
            paths = analysis_state_paths(fixed)
            index = read_evidence_index(evidence)
            native_entries = sorted(
                (
                    entry
                    for entry in index.entries
                    if entry["bundle_state"] == "native"
                ),
                key=lambda entry: entry["cycle_ordinal"],
            )
            legacy_entry = next(
                entry
                for entry in index.entries
                if entry["bundle_state"] == "legacy_unknown"
            )
            optional_recipe = default_recipe(
                inference_mode="codex_optional",
                declared_model_id="fixture-model",
            )
            offline_source = validate_evidence_source(index, native_entries[0])
            offline = enqueue_analysis_source(
                paths=paths,
                source=offline_source,
                recipe=default_recipe(),
            )
            optional_source = validate_evidence_source(index, native_entries[1])
            optional = enqueue_analysis_source(
                paths=paths,
                source=optional_source,
                recipe=optional_recipe,
            )
            plan = ClosedMockPlan((MockScenario.TIMEOUT, MockScenario.SUCCESS))
            mixed_results = {}
            for _ in range(2):
                result = run_once(
                    analysis_state_paths(fixed),
                    read_evidence_index(evidence),
                    plan,
                )
                mixed_results[result.analysis_job_id] = result
            offline_result = mixed_results[offline.descriptor["analysis_job_id"]]
            mocked = mixed_results[optional.descriptor["analysis_job_id"]]
            self.assertEqual(offline_result.disposition, "published")
            self.assertEqual(offline_result.status["inference_state"], "disabled")
            self.assertEqual(offline_result.status["adapter_attempt_count"], 0)
            self.assertEqual(mocked.disposition, "published")
            self.assertEqual(mocked.status["inference_state"], "succeeded")
            self.assertEqual(mocked.status["adapter_attempt_count"], 2)
            self.assertIsNotNone(mocked.status["accepted_output_digest"])

            legacy_source = validate_evidence_source(index, legacy_entry)
            legacy = enqueue_analysis_source(
                paths=analysis_state_paths(fixed),
                source=legacy_source,
                recipe=optional_recipe,
            )
            blocked = run_once(
                analysis_state_paths(fixed),
                read_evidence_index(evidence),
            )
            self.assertEqual(blocked.analysis_job_id, legacy.descriptor["analysis_job_id"])
            self.assertEqual(blocked.disposition, "published")
            self.assertEqual(blocked.status["inference_state"], "policy_blocked")
            self.assertEqual(blocked.status["adapter_attempt_count"], 0)
            self.assertIsNone(blocked.status["accepted_output_digest"])

            mocked_report = self._report_payload(
                paths,
                mocked.status,
                descriptor=optional.descriptor,
                expected_recipe=optional_recipe,
                source=optional_source,
            )
            blocked_report = self._report_payload(
                paths,
                blocked.status,
                descriptor=legacy.descriptor,
                expected_recipe=optional_recipe,
                source=legacy_source,
            )
            self.assertNotIn(
                "inference_unavailable",
                {item["code"] for item in mocked_report["omissions"]},
            )
            self.assertEqual(
                {item["code"] for item in blocked_report["omissions"]},
                {"legacy_detail_unavailable", "inference_unavailable"},
            )
            self.assertEqual(blocked_report["structural_facts"], [])
            self.assertEqual(blocked_report["trusted_caller_declarations"], [])
            self.assertEqual(blocked_report["llm_derived"], [])
            self.assertEqual(tree_snapshot(evidence), evidence_before)
            self._assert_sentinels_unchanged(sentinels)
            self._assert_clean_analysis_tree(
                paths,
                job_ids={
                    str(offline.descriptor["analysis_job_id"]),
                    str(optional.descriptor["analysis_job_id"]),
                    str(legacy.descriptor["analysis_job_id"]),
                },
                report_ids={
                    str(offline_result.status["report_id"]),
                    str(mocked.status["report_id"]),
                    str(blocked.status["report_id"]),
                },
            )

    def test_worker_import_closure_excludes_storage_cli_and_network_modules(self):
        forbidden = (
            "sqlite3",
            "socket",
            "subprocess",
            "requests",
            "urllib.request",
            "http.client",
            "task_governance_tool.storage",
            "task_governance_tool.tasks",
            "task_governance_tool.cli",
            "task_governance_tool.setup",
            "task_governance_tool.maintenance",
            "task_governance_tool.evidence_projection",
        )
        script = f"""
import json
import sys

forbidden = {forbidden!r}
denied = []
allowed = []

class DeniedImport(RuntimeError):
    pass

class ImportGuard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {{"urllib", "urllib.parse"}}:
            allowed.append(fullname)
            return None
        if fullname.startswith("urllib.") or any(
            fullname == item or fullname.startswith(item + ".")
            for item in forbidden
        ):
            denied.append(fullname)
            raise DeniedImport(fullname)
        return None

sys.meta_path.insert(0, ImportGuard())
sys.path.insert(0, {str(SCRIPTS_ROOT)!r})
import task_governance_tool.analysis_worker
__import__("urllib.parse", fromlist=("*",))
worker_denials = list(denied)

fixture_denials = []
for name in forbidden:
    denied.clear()
    try:
        __import__(name, fromlist=("*",))
    except DeniedImport as exc:
        if exc.args != (name,) or denied != [name]:
            raise
        fixture_denials.append(name)
    else:
        raise AssertionError(name)

print(json.dumps({{
    "allowed": sorted(set(allowed)),
    "fixture_denials": fixture_denials,
    "worker_denials": worker_denials,
}}, separators=(",", ":")))
"""
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "allowed": ["urllib", "urllib.parse"],
                "fixture_denials": list(forbidden),
                "worker_denials": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
