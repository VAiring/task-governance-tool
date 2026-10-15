"""Candidate-core registration with real repositories and native source digests.

Fixtures use isolated schema-28 setup followed by explicit candidate migration;
the shipped admission ceiling and the user's canonical state are unchanged.
"""

from __future__ import annotations

import tempfile
import copy
import hashlib
import json
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests.verification_receipt_test_support import initialize, add_task, set_target, target_for, run_taskgov, payload, completion
from tests.test_m23s_schema22_validation import _bundle_artifacts
from tests.test_review_session_migration import candidate_runtime
from tests.test_review_results import document, receipt, encode
from tests.test_review_continuation import A, B, ident, basis
from task_governance_tool import storage, review_results, reviews, review_session_transport as transport
from task_governance_tool import review_continuation as values, review_continuation_repository as repository
from task_governance_tool import schema_review_continuation as schema
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.task_ownership import read_basis
from task_governance_tool import review_continuation_evidence as frozen
from task_governance_tool.review_continuation_evidence_repository import freeze_basis_locked
from task_governance_tool.evidence_ledger import canonical_json_bytes, EvidenceLedgerError


class ContinuationRegistrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="taskgov-continuation-registration-")
        self.addCleanup(temporary.cleanup)
        with candidate_runtime(28):
            self.repo, self.db = initialize(Path(temporary.name))
            self.task = add_task(self.db, self.repo, review_tier=2)
            self.task_id = self.task["task_id"]
            set_target(self.db, self.repo, self.task_id)
        self.enterContext(candidate_runtime(29))
        with closing(storage.connect(self.db)) as connection:
            schema.migrate(connection)
        self.target = target_for(self.db, self.repo)
        with closing(storage.connect_initialized(self.target)) as connection:
            project_meta = connection.execute("SELECT canonical_path_hash,binding_generation FROM project_meta WHERE project_id=?", (self.target.project.project_id,)).fetchone()
            # Explicit-path test targets omit these optional observations. Bind
            # them to the admitted fixture's actual metadata, as public fixed
            # state resolution does; do not relax the production value contract.
            self.target = replace(self.target, binding_path_hash=project_meta["canonical_path_hash"],
                                  binding_generation=project_meta["binding_generation"])
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (self.task_id,)).fetchone()
            ownership = read_basis(connection, project_id=task["project_id"], task_id=self.task_id)
            self.caller = CallerIdentity(ownership.owner_session_id)
            self.basis = basis(project_id=task["project_id"], task_id=self.task_id,
                contract_revision=task["current_contract_revision"], authority_snapshot_id=task["review_target_authority_snapshot_id"],
                acceptance_criterion_id=task["review_target_acceptance_criterion_id"], verification_criterion_id=task["review_target_verification_criterion_id"],
                artifact_manifest_id=task["review_target_artifact_manifest_id"],
                target_kind=task["review_target_kind"], target_value=task["review_target_value"],
                target_base_revision=task["review_target_base_revision"], target_generation=task["review_target_generation"],
                project_path_hash=self.target.binding_path_hash, project_binding_generation=self.target.binding_generation,
                parent_session_id=ownership.owner_session_id, execution_id=ownership.execution_id, ownership_generation=ownership.generation)
        self.packet = dict(task={"task_id": self.task_id}, contract={"revision": self.basis["contract_revision"]},
            review_target={key: self.basis["target_" + key] for key in ("kind", "value", "base_revision", "generation")},
            review_session_context=dict(version=1, project_id=self.basis["project_id"], execution_id=self.basis["execution_id"]))

    def original(self, actor, *, verdict="pass", findings=(), continued=None):
        raw = encode(document(self.task_id, revision=self.basis["contract_revision"],
            generation=self.basis["target_generation"], receipts=[receipt("a" if actor == A else "b", verdict=verdict, findings=findings)]))
        packet = dict(self.packet)
        if continued is not None:
            packet["review_session_context"] = dict(version=2, project_id=self.basis["project_id"], execution_id=self.basis["execution_id"],
                preparation_id=continued.preparation_id, continuation_id=continued.continuation_id,
                continuation_sequence=continued.sequence, selected_reviewers=[
                    dict(session_id=row.session_id, action=row.action, prior_receipt_id=row.prior_receipt_id) for row in continued.selected])
        return raw, transport.metadata_for(packet, raw, CallerIdentity(actor))

    def register(self, originals, *, continued=None, retained=()):
        identity = transport.ContinuationIdentity(continued.preparation_id, continued.continuation_id, continued.sequence) if continued else None
        decoded = transport.decode_submission(transport.frame_submission([row[0] for row in originals], [row[1] for row in originals],
            continuation=identity, retained_receipt_ids=retained))
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            return review_results.add_review_results(connection, self.target.project, self.task_id, decoded.document,
                database_target=self.target, caller=self.caller, session_bindings=decoded.bindings,
                continuation_identity=decoded.continuation, retained_receipt_ids=decoded.retained_receipt_ids)

    def prepare(self, *, actor=A, prior=None, selected=None):
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            record = repository.admit_locked(connection, basis=self.basis, preparation_id=ident("tg_review_preparation_"),
                route="manual", selected=selected or (values.SelectedReviewer(actor, "reassess" if prior else "supplement", prior, "Explicit result continuation"),), caller=self.caller)
            return repository.advance_prepared_locked(connection, record, self.caller)

    def evidence(self, *, detail=None):
        with closing(storage.connect_initialized(self.target)) as connection:
            return reviews.read_review_evidence(connection, self.basis["project_id"], self.task_id,
                source_schema_version=29, task_show_details=detail)

    def resolve(self, finding_id, *, successor=None, summary="Explicit finding resolution"):
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            return reviews.resolve_review_finding(connection, self.target.project, finding_id, resolution=summary,
                database_target=self.target, caller=self.caller, reassessment_receipt=successor)

    def freeze(self):
        with closing(storage.connect_initialized(self.target)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (self.task_id,)).fetchone()
            value = freeze_basis_locked(connection, task)
            connection.rollback()
            return value

    def reassess_with_finding(self):
        initial = self.register([self.original(A, verdict="changes_requested", findings=[dict(severity="medium", summary="Reconsider this precise condition")]), self.original(B)])
        old = initial["receipts"][0]["receipt"]["review_receipt_id"]
        finding = initial["receipts"][0]["findings"][0]["finding"]["review_finding_id"]
        retained = initial["receipts"][1]["receipt"]["review_receipt_id"]
        record = self.prepare(prior=old)
        result = self.register([self.original(A, continued=record)], continued=record, retained=(retained,))
        return old, finding, result["receipts"][0]["receipt"]["review_receipt_id"]

    def test_ordinary_native_registration_creates_revision_one_and_preserves_gate(self):
        result = self.register([self.original(A), self.original(B)])
        self.assertTrue(result["review_gate"]["satisfied"])
        self.assertEqual([row["receipt"]["judgment_revision"] for row in result["receipts"]], [1, 1])
        with closing(storage.connect_initialized(self.target)) as connection:
            schema.validate_storage(connection)

    def test_unjudged_supplement_keeps_registered_other_pass(self):
        prior = self.register([self.original(B)])["receipts"][0]["receipt"]["review_receipt_id"]
        record = self.prepare()
        result = self.register([self.original(A, continued=record)], continued=record, retained=(prior,))
        self.assertTrue(result["review_gate"]["satisfied"])
        with closing(storage.connect_initialized(self.target)) as connection:
            records = repository.read_continuations(connection, project_id=self.basis["project_id"], task_id=self.task_id)
            self.assertEqual(records[0].state, "registered")
            self.assertEqual(connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 2)
            schema.validate_storage(connection)

    def test_reassessment_replaces_effective_verdict_but_preserves_ng_and_finding(self):
        initial = self.register([self.original(A, verdict="changes_requested", findings=[dict(severity="medium", summary="Reconsider this precise condition")]), self.original(B)])
        old = initial["receipts"][0]["receipt"]["review_receipt_id"]
        retained = initial["receipts"][1]["receipt"]["review_receipt_id"]
        record = self.prepare(prior=old)
        result = self.register([self.original(A, continued=record)], continued=record, retained=(retained,))
        self.assertFalse(result["review_gate"]["satisfied"])
        with closing(storage.connect_initialized(self.target)) as connection:
            graph = repository.read_judgments(connection, basis=self.basis)
            effective = values.select_effective(graph, quality_basis=values.quality_identity(self.basis))
            self.assertEqual([row.verdict for row in effective.active], ["pass", "pass"])
            self.assertEqual(connection.execute("SELECT verdict,judgment_revision FROM review_receipts WHERE review_receipt_id=?", (old,)).fetchone()["verdict"], "changes_requested")
            self.assertEqual(connection.execute("SELECT status FROM review_findings WHERE review_receipt_id=?", (old,)).fetchone()[0], "open")
            schema.validate_storage(connection)

    def test_known_registration_replay_never_creates_another_receipt_or_event(self):
        prior = self.register([self.original(B)])["receipts"][0]["receipt"]["review_receipt_id"]
        record = self.prepare()
        original = self.original(A, continued=record)
        first = self.register([original], continued=record, retained=(prior,))
        with closing(storage.connect_initialized(self.target)) as connection:
            before = storage._selected_table_projection_snapshot(connection, ("review_receipts", "review_receipt_provenance", "review_receipt_judgments", "review_continuations", "review_continuation_reviewers", "task_events"))
        replay = self.register([original], continued=record, retained=(prior,))
        self.assertEqual(first["receipts"][0]["receipt"]["review_receipt_id"], replay["receipts"][0]["receipt"]["review_receipt_id"])
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tuple(before)), before)

    def test_missing_retained_result_rejected_before_new_writes(self):
        prior = self.register([self.original(B)])["receipts"][0]["receipt"]["review_receipt_id"]
        record = self.prepare()
        with closing(storage.connect_initialized(self.target)) as connection:
            before = storage._selected_table_projection_snapshot(connection, ("review_receipts", "review_receipt_judgments", "review_continuations", "task_events"))
        with self.assertRaises(Exception) as caught:
            self.register([self.original(A, continued=record)], continued=record)
        self.assertEqual(caught.exception.code, "review_continuation_conflict")
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tuple(before)), before)
            self.assertIsNotNone(connection.execute("SELECT 1 FROM review_receipts WHERE review_receipt_id=?", (prior,)).fetchone())

    def test_failure_after_first_source_write_rolls_back_whole_batch(self):
        record = self.prepare(selected=(values.SelectedReviewer(A, "supplement", None, "Missing A"),
                                        values.SelectedReviewer(B, "supplement", None, "Missing B")))
        with closing(storage.connect_initialized(self.target)) as connection:
            tables = tuple(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"))
            before = storage._selected_table_projection_snapshot(connection, tables)
        actual_add = review_results.add_review_receipt
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(kwargs["reviewer"])
            if len(calls) == 2:
                raise RuntimeError("isolated mid-batch interruption")
            return actual_add(*args, **kwargs)
        with mock.patch.object(review_results, "add_review_receipt", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.register([self.original(A, continued=record), self.original(B, continued=record)], continued=record)
        self.assertEqual(calls, ["a", "b"])
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tables), before)
            schema.validate_storage(connection)

    def test_two_selected_supplements_register_as_one_complete_round(self):
        record = self.prepare(selected=(values.SelectedReviewer(A, "supplement", None, "Missing A"),
                                        values.SelectedReviewer(B, "supplement", None, "Missing B")))
        result = self.register([self.original(A, continued=record), self.original(B, continued=record)], continued=record)
        self.assertTrue(result["review_gate"]["satisfied"])
        with closing(storage.connect_initialized(self.target)) as connection:
            current = repository.read_continuations(connection, project_id=self.basis["project_id"], task_id=self.task_id)[0]
            self.assertEqual(current.state, "registered")
            self.assertEqual(len(current.results), 2)
            schema.validate_storage(connection)

    def test_identical_result_bytes_in_two_revisions_reconcile_by_linked_id(self):
        initial = self.register([self.original(A), self.original(B)])
        old = initial["receipts"][0]["receipt"]["review_receipt_id"]
        retained = initial["receipts"][1]["receipt"]["review_receipt_id"]
        record = self.prepare(prior=old)
        new_original = self.original(A, continued=record)
        self.assertEqual(new_original[0], self.original(A)[0])
        result = self.register([new_original], continued=record, retained=(retained,))
        replay = self.register([new_original], continued=record, retained=(retained,))
        self.assertNotEqual(result["receipts"][0]["receipt"]["review_receipt_id"], old)
        self.assertEqual(replay["receipts"][0]["receipt"]["review_receipt_id"], result["receipts"][0]["receipt"]["review_receipt_id"])
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM review_receipts").fetchone()[0], 3)

    def test_pending_reassessment_blocks_done_even_with_old_passes(self):
        initial = self.register([self.original(A), self.original(B)])
        old = initial["receipts"][0]["receipt"]["review_receipt_id"]
        record = self.prepare(prior=old)
        evidence = self.evidence()
        self.assertFalse(evidence["gate"]["satisfied"])
        self.assertEqual(evidence["gate"]["qualifying_independent_passes"], 1)
        self.assertEqual(evidence["continuation"]["pending_continuation_id"], record.continuation_id)
        self.assertEqual(reviews.first_review_gate_error(evidence).code, "review_continuation_pending")

    def test_current_projection_uses_heads_while_recent_history_keeps_ng(self):
        old, finding, successor = self.reassess_with_finding()
        detail = reviews.TaskShowReviewDetails()
        evidence = self.evidence(detail=detail)
        self.assertEqual(evidence["counts"]["changes_requested_current_generation"], 0)
        self.assertEqual(evidence["gate"]["qualifying_independent_passes"], 2)
        self.assertNotIn(old, [row["review_receipt_id"] for row in detail.receipts])
        self.assertIn(successor, [row["review_receipt_id"] for row in detail.receipts])
        self.assertIn(old, [row["review_receipt_id"] for row in evidence["recent_receipts"]])
        self.assertEqual(evidence["counts"]["open_medium"], 1)
        self.assertFalse(evidence["gate"]["satisfied"])

    def test_explicit_typed_resolution_permits_same_generation_without_erasing_history(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding, successor=successor)
        evidence = self.evidence()
        self.assertTrue(evidence["gate"]["satisfied"])
        self.assertEqual(evidence["blocking_findings"], [])
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(connection.execute("SELECT verdict FROM review_receipts WHERE review_receipt_id=?", (old,)).fetchone()[0], "changes_requested")
            self.assertEqual(connection.execute("SELECT count(*) FROM review_reassessment_resolutions WHERE review_finding_id=?", (finding,)).fetchone()[0], 1)
            schema.validate_storage(connection)

    def test_records_only_reassessment_link_keeps_existing_mutation_boundaries(self):
        from task_governance_tool.task_record_policy import validate_record_arguments, RecordPolicyError

        old, finding, successor = self.reassess_with_finding()
        for arguments in (
            ["review", "finding", "resolve", finding, "--resolution", "Confirmed reassessment"],
            ["review", "finding", "resolve", "--from-stdin"],
        ):
            selected = ["--records-only", *arguments, "--repo", str(self.repo), "--reassessment-receipt", successor]
            self.assertEqual(validate_record_arguments("taskgov.py", selected), str(self.repo))
            with self.assertRaises(RecordPolicyError):
                validate_record_arguments("taskgov.py", selected + ["--reassessment-receipt", successor])
        for script, arguments in (
            ("taskgov.py", ["setup"]),
            ("taskgov.py", ["task", "edit", self.task_id, "--runner-plan-action", "disable"]),
            ("review_handoff.py", ["finalize"]),
            ("review_handoff.py", ["prepare-finalization"]),
        ):
            with self.subTest(script=script, arguments=arguments), self.assertRaises(RecordPolicyError):
                validate_record_arguments(script, ["--records-only", *arguments, "--repo", str(self.repo)])
        result = run_taskgov("--records-only", "review", "finding", "resolve", finding,
            "--resolution", "Actual reviewer reconsidered this condition", "--reassessment-receipt", successor,
            "--repo", str(self.repo), "--db", str(self.db), "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.evidence()["gate"]["satisfied"])
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(connection.execute("SELECT verdict FROM review_receipts WHERE review_receipt_id=?", (old,)).fetchone()[0], "changes_requested")
            self.assertEqual(connection.execute("SELECT successor_receipt_id FROM review_reassessment_resolutions WHERE review_finding_id=?", (finding,)).fetchone()[0], successor)

    def test_already_resolved_finding_can_link_without_overwriting_original_reason(self):
        old, finding, successor = self.reassess_with_finding()
        first = self.resolve(finding, summary="Original explicit resolution").finding
        self.assertFalse(self.evidence()["gate"]["satisfied"])
        linked = self.resolve(finding, successor=successor, summary="Actual reviewer reconsidered this condition").finding
        self.assertEqual(linked["resolution_summary"], first["resolution_summary"])
        self.assertEqual(linked["resolved_at"], first["resolved_at"])
        self.assertTrue(self.evidence()["gate"]["satisfied"])

    def test_unlinked_resolution_keeps_fresh_review_requirement(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding)
        evidence = self.evidence()
        self.assertFalse(evidence["gate"]["satisfied"])
        self.assertEqual(evidence["blocking_findings"][0]["blocking_reason"], "fresh_review_required")

    def test_unrelated_successor_link_rolls_back_resolution_and_events(self):
        old, finding, successor = self.reassess_with_finding()
        other_id = next(row["review_receipt_id"] for row in self.evidence()["recent_receipts"] if row["reviewer_key"] == "b")
        with closing(storage.connect_initialized(self.target)) as connection:
            before = storage._selected_table_projection_snapshot(connection, ("review_findings", "review_reassessment_resolutions", "task_events"))
        with self.assertRaises(Exception):
            self.resolve(finding, successor=other_id)
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tuple(before)), before)

    def test_duplicate_explicit_link_cannot_add_another_resolution_event(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding, successor=successor)
        with closing(storage.connect_initialized(self.target)) as connection:
            before = storage._selected_table_projection_snapshot(connection, ("review_findings", "review_reassessment_resolutions", "task_events"))
        with self.assertRaises(Exception):
            self.resolve(finding, successor=successor)
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(storage._selected_table_projection_snapshot(connection, tuple(before)), before)

    def test_frozen_graph_keeps_original_ng_and_explicit_resolution(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding, successor=successor)
        graph = self.freeze()
        self.assertEqual(len(graph["receipts"]), 3)
        self.assertEqual(next(row["verdict"] for row in graph["receipts"] if row["review_receipt_id"] == old), "changes_requested")
        self.assertNotIn(old, graph["effective_receipt_ids"])
        self.assertIn(successor, graph["effective_receipt_ids"])
        self.assertEqual(len(graph["resolutions"]), 1)
        self.assertEqual(frozen.decode_graph_json(canonical_json_bytes(graph).decode()), graph)

    def test_frozen_graph_rejects_invented_heads_missing_ancestry_and_resolution_digest(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding, successor=successor)
        graph = self.freeze()
        changed_head = copy.deepcopy(graph)
        changed_head["effective_receipt_ids"][0] = old
        missing_ancestor = copy.deepcopy(graph)
        missing_ancestor["receipts"] = [row for row in graph["receipts"] if row["review_receipt_id"] != old]
        bad_resolution = copy.deepcopy(graph)
        bad_resolution["resolutions"][0]["resolution_digest"] = "0" * 64
        for changed in (changed_head, missing_ancestor, bad_resolution):
            with self.subTest(changed=list(changed)), self.assertRaises(EvidenceLedgerError):
                frozen.validate_graph(changed)

    def test_freeze_rejects_unlinked_same_generation_resolved_finding(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding)
        with self.assertRaises(EvidenceLedgerError):
            self.freeze()

    def native_continuation_artifact(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding, successor=successor)
        recorded = run_taskgov("verification", "receipt", "add", self.task_id,
            "--result", "pass", "--duration-ms", "1", "--scope-coverage", "full",
            "--expected-target-generation", str(self.basis["target_generation"]),
            "--repo", str(self.repo), "--db", str(self.db), "--json")
        self.assertEqual(recorded.returncode, 0, recorded.stdout + recorded.stderr)
        result = completion(self.db, self.repo, self.task_id)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(payload(result)["ok"])
        with closing(storage.connect_initialized(self.target)) as connection:
            schema.validate_storage(connection)
            bundle = connection.execute("SELECT * FROM completion_evidence_bundles WHERE task_id=?", (self.task_id,)).fetchone()
            self.assertEqual(bundle["bundle_version"], 3)
            self.assertIsNotNone(bundle["continuation_basis_json"])
            graph = frozen.decode_graph_json(bundle["continuation_basis_json"])
            self.assertIn(successor, graph["effective_receipt_ids"])
            self.assertNotIn(old, graph["effective_receipt_ids"])
            _, artifacts = _bundle_artifacts(connection, self.basis["project_id"])
            artifact = artifacts[bundle["completion_evidence_bundle_id"]]
            self.assertEqual(artifact.payload["review_continuation_basis"], graph)
            from tests.evidence_reader_oracle import ValidatedEvidenceSource
            entry = dict(task_id=self.task_id, completion_cycle_id=bundle["completion_cycle_id"],
                cycle_ordinal=bundle["cycle_ordinal"], bundle_state="native", bundle_id=bundle["completion_evidence_bundle_id"],
                bundle_file="bundles/" + bundle["completion_evidence_bundle_id"] + ".json", bundle_digest=artifact.bundle_digest,
                file_digest=artifact.file_digest, sealed_at=bundle["sealed_at"], bundle_format_version=3)
            source_basis = dict(index_format_version=2, source_schema_version=29, project_id=self.basis["project_id"],
                projection_generation=1, index_digest="sha256:" + "0" * 64, entry=entry)
            source = ValidatedEvidenceSource("native_bundle", source_basis, artifact.envelope)
            self.assertEqual(source.source["payload"]["review_continuation_basis"], graph)
            return artifact, source_basis

    def test_native_completion_seals_format_three_and_reentry_reuses_frozen_graph(self):
        self.native_continuation_artifact()

    def test_independent_reader_rejects_resealed_continuation_binding_corruption(self):
        from tests import evidence_reader_oracle as reader

        artifact, source_basis = self.native_continuation_artifact()

        def changed_basis(graph, field, value):
            for row in graph["continuations"]:
                basis = json.loads(row["basis_json"])
                basis[field] = value
                row["basis_json"] = reader.canonical_json_bytes(basis).decode()
                row["basis_digest"] = reader._domain_digest(
                    b"taskgov-review-continuation-basis-v1\0", basis)[7:]

        def reseal_reference(graph, reference):
            kind, identity = reference["source_kind"], reference["source_id"]
            if kind == "review_receipt":
                row = next(row for row in graph["receipts"] if row["review_receipt_id"] == identity)
                assertion = {key: value for key, value in row.items() if key != "judgment_revision"}
            else:
                row = next(row for row in graph["findings"] if row["review_finding_id"] == identity)
                assertion = {key: row[key] for key in (
                    "review_finding_id", "review_receipt_id", "severity", "summary", "created_at")}
            sealed = {key: reference[key] for key in reader._REFERENCE_KEYS
                if key not in ("evidence_reference_id", "digest")}
            sealed.update(project_id=artifact.payload["project_id"], task_id=self.task_id,
                source_projection=assertion, target_base_revision=reference["target_base_revision"] or "")
            reference["digest"] = reader._domain_digest(reader.EVIDENCE_REFERENCE_DOMAIN, sealed)

        def changed_reference_base(graph):
            reference = graph["references"][0]
            reference["target_base_revision"] = "f" * 40
            reseal_reference(graph, reference)

        def changed_finding(graph, field, value):
            finding = graph["findings"][0]
            finding[field] = value
            reference = next(row for row in graph["references"]
                if row["source_kind"] == "review_finding" and row["source_id"] == finding["review_finding_id"])
            reseal_reference(graph, reference)

        mutations = {
            "reference base differs from all other target members": changed_reference_base,
            "parent session is not a canonical identity": lambda graph: changed_basis(graph, "parent_session_id", "invalid"),
            "execution identity is malformed": lambda graph: changed_basis(graph, "execution_id", "invalid"),
            "ownership generation exceeds SQLite integer": lambda graph: changed_basis(graph, "ownership_generation", 1 << 63),
            "commit declaration is a boolean": lambda graph: changed_basis(graph, "completion_commit_required", True),
            "verification declaration disagrees with bundle": lambda graph: changed_basis(graph, "verification_requirement", "unspecified"),
            "ordinary session execution identity is malformed": lambda graph: graph["session_bindings"][0].update(execution_id="invalid"),
            "handoff digest is not a digest": lambda graph: graph["session_bindings"][0].update(original_result_digest="invalid"),
            "reference identity is malformed": lambda graph: graph["references"][0].update(evidence_reference_id="invalid"),
            "reference producer version is a boolean": lambda graph: graph["references"][0].update(producer_version=True),
            "reference generation is a boolean": lambda graph: graph["references"][0].update(target_generation=True),
            "reference Contract revision is a boolean": lambda graph: graph["references"][0].update(contract_revision=False),
            "reference base is a boolean": lambda graph: graph["references"][0].update(target_base_revision=False),
            "reference base is numeric": lambda graph: graph["references"][0].update(target_base_revision=0),
            "reference base is an array": lambda graph: graph["references"][0].update(target_base_revision=[]),
            "finding severity is outside the closed vocabulary": lambda graph: changed_finding(graph, "severity", "informational"),
            "finding creation timestamp is malformed": lambda graph: changed_finding(graph, "created_at", "invalid"),
            "open finding retains resolved fields": lambda graph: changed_finding(graph, "status", "open"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(artifact.payload)
                mutate(changed["review_continuation_basis"])
                with self.assertRaises(EvidenceLedgerError):
                    frozen.validate_graph(changed["review_continuation_basis"], bundle=changed)
                # Recompute outer seals so rejection proves graph validation,
                # rather than merely noticing a stale checksum.
                digest = reader._domain_digest(reader.BUNDLE_V3_DOMAIN, changed)
                envelope = dict(format_version=3, bundle_digest=digest, payload=changed)
                basis = copy.deepcopy(source_basis)
                basis["entry"].update(bundle_digest=digest,
                    file_digest="sha256:" + hashlib.sha256(reader.canonical_json_bytes(envelope) + b"\n").hexdigest())
                with self.assertRaises(reader.EvidenceConsumerError):
                    reader.ValidatedEvidenceSource("native_bundle", basis, envelope)


class ContinuationEvidenceValidationTests(unittest.TestCase):
    """Responsibility boundaries and their public whole-graph composition."""

    setUp = ContinuationRegistrationTests.setUp
    original = ContinuationRegistrationTests.original
    register = ContinuationRegistrationTests.register
    prepare = ContinuationRegistrationTests.prepare
    resolve = ContinuationRegistrationTests.resolve
    freeze = ContinuationRegistrationTests.freeze
    reassess_with_finding = ContinuationRegistrationTests.reassess_with_finding
    native_continuation_artifact = ContinuationRegistrationTests.native_continuation_artifact

    def settled_graph(self):
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding, successor=successor)
        return self.freeze()

    def assert_rejected_by_boundary_and_graph(self, graph, boundary):
        # Unit checks name the responsibility; public checks prevent a missed
        # helper invocation from silently accepting the same bad evidence.
        for validate in (boundary, frozen.validate_graph):
            before = canonical_json_bytes(graph)
            with self.assertRaises((EvidenceLedgerError, values.TaskValidationError)) as caught:
                validate(graph)
            self.assertEqual(caught.exception.code, "evidence_ledger_inconsistent")
            self.assertEqual(canonical_json_bytes(graph), before)

    @staticmethod
    def receipt_parts(graph):
        rows = frozen._index_graph(graph)
        history = frozen._validate_history(rows)
        references = frozen._index_references(rows)
        return rows, history, references

    def test_closed_row_indexes_reject_missing_duplicate_unsorted_and_wrong_types(self):
        graph = self.settled_graph()
        mutations = {
            "boolean version": lambda value: value.update(version=True),
            "non-list receipts": lambda value: value.update(receipts={}),
            "duplicate receipt": lambda value: value["receipts"].append(value["receipts"][0]),
            "unsorted receipts": lambda value: value["receipts"].reverse(),
            "missing judgment": lambda value: value["judgments"].pop(),
            "extra field": lambda value: value["receipts"][0].update(extra=None),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(graph)
                mutate(changed)
                self.assert_rejected_by_boundary_and_graph(changed, frozen._index_graph)

    def test_history_rejects_wrong_ownership_sequence_and_pending_rounds(self):
        graph = self.settled_graph()
        changes = (
            dict(task_id=ident("tg_task_", 900)), dict(target_generation=2),
            dict(sequence=True), dict(sequence=2),
            dict(predecessor_id=graph["continuations"][0]["continuation_id"]),
            dict(state="prepared"), dict(selection_digest="0" * 64),
        )
        for change in changes:
            with self.subTest(change=change):
                changed = copy.deepcopy(graph)
                changed["continuations"][0].update(change)
                self.assert_rejected_by_boundary_and_graph(changed,
                    lambda value: frozen._validate_history(frozen._index_graph(value)))

    def test_receipt_reference_and_session_checks_reject_substitution(self):
        graph = self.settled_graph()
        selected_id = graph["reviewers"][0]["result_receipt_id"]

        def session_change(value):
            next(row for row in value["session_bindings"] if row["review_receipt_id"] == selected_id).update(session_id=B)

        mutations = {
            "missing Receipt reference": lambda value: value["references"].__setitem__(slice(None),
                [row for row in value["references"] if row["source_kind"] != "review_receipt"]),
            "changed assertion": lambda value: value["receipts"][0].update(summary="Unattested replacement"),
            "boolean revision": lambda value: value["receipts"][0].update(judgment_revision=True),
            "wrong actual session": session_change,
            "boolean reference version": lambda value: [row.update(producer_version=True)
                for row in value["references"] if row["source_kind"] == "review_receipt"],
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(graph)
                mutate(changed)
                self.assert_rejected_by_boundary_and_graph(changed,
                    lambda value: frozen._validate_receipts(*self.receipt_parts(value)))

    def test_effective_heads_are_recomputed_from_complete_judgment_ancestry(self):
        graph = self.settled_graph()
        old = next(row["review_receipt_id"] for row in graph["receipts"] if row["verdict"] == "changes_requested")

        def validate_heads(value):
            rows, history, references = self.receipt_parts(value)
            nodes = frozen._validate_receipts(rows, history, references)
            return frozen._validate_effective_heads(nodes, history.quality, value["effective_receipt_ids"])

        for heads in ([], [old], list(reversed(graph["effective_receipt_ids"])), tuple(graph["effective_receipt_ids"])):
            with self.subTest(heads=heads):
                changed = copy.deepcopy(graph)
                changed["effective_receipt_ids"] = heads
                # Tuple is deliberately outside canonical JSON's input types.
                if type(heads) is tuple:
                    with self.assertRaises(EvidenceLedgerError):
                        validate_heads(changed)
                    with self.assertRaises(EvidenceLedgerError):
                        frozen.validate_graph(changed)
                else:
                    self.assert_rejected_by_boundary_and_graph(changed, validate_heads)

    def test_finding_assertions_and_explicit_resolution_links_are_separate_gates(self):
        graph = self.settled_graph()
        changed = copy.deepcopy(graph)
        changed["findings"][0]["summary"] = "Changed original finding"

        def validate_findings(value):
            rows, history, references = self.receipt_parts(value)
            frozen._validate_findings(rows, history.basis, references)

        self.assert_rejected_by_boundary_and_graph(changed, validate_findings)

        def validate_resolutions(value):
            rows, history, references = self.receipt_parts(value)
            frozen._validate_resolutions(rows, frozen._validate_receipts(rows, history, references))

        other = next(row["review_receipt_id"] for row in graph["receipts"] if row["reviewer_key"] == "b")
        mutations = {
            "resolved without link": lambda value: value.update(resolutions=[]),
            "wrong successor ancestry": lambda value: value["resolutions"][0].update(successor_receipt_id=other),
            "altered resolution seal": lambda value: value["resolutions"][0].update(resolution_digest="0" * 64),
            "unresolved finding": lambda value: value["findings"][0].update(status="open", resolution_summary="", resolved_at=None),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(graph)
                mutate(changed)
                self.assert_rejected_by_boundary_and_graph(changed, validate_resolutions)

    def test_bundle_matching_preserves_exact_bytes_and_independent_reader_boundary(self):
        from tests.evidence_reader_oracle import ValidatedEvidenceSource

        artifact, source_basis = self.native_continuation_artifact()
        bundle = artifact.payload
        graph = bundle["review_continuation_basis"]
        before_graph, before_bundle = canonical_json_bytes(graph), canonical_json_bytes(bundle)
        self.assertIs(frozen.validate_graph(graph, bundle=bundle), graph)
        self.assertEqual(frozen.decode_graph_json(before_graph.decode()), graph)
        with self.assertRaises(EvidenceLedgerError):
            frozen.decode_graph_json(json.dumps(graph, indent=2))
        with mock.patch.object(frozen, "validate_graph", side_effect=AssertionError("producer validator called by oracle")):
            self.assertEqual(ValidatedEvidenceSource("native_bundle", source_basis, artifact.envelope).source["payload"], bundle)
        self.assertEqual(canonical_json_bytes(graph), before_graph)
        self.assertEqual(canonical_json_bytes(bundle), before_bundle)

        rows, history, references = self.receipt_parts(graph)
        nodes = frozen._validate_receipts(rows, history, references)
        effective = frozen._validate_effective_heads(nodes, history.quality, graph["effective_receipt_ids"])
        for name, mutate, boundary in (
            ("wrong Task", lambda value: value["task"].update(task_id=ident("tg_task_", 900)),
             lambda value: frozen._match_bundle_basis(value, history.basis)),
            ("wrong target generation", lambda value: value["target"].update(generation=2),
             lambda value: frozen._match_bundle_basis(value, history.basis)),
            ("wrong selection order", lambda value: value["review_receipts"].reverse(),
             lambda value: frozen._match_bundle_receipts(value, rows, history, effective)),
            ("wrong selected assertion", lambda value: value["review_receipts"][0].update(summary="Changed bundle assertion"),
             lambda value: frozen._match_bundle_receipts(value, rows, history, effective)),
        ):
            with self.subTest(name=name):
                changed = copy.deepcopy(bundle)
                mutate(changed)
                for validate in (boundary, lambda value: frozen.validate_graph(graph, bundle=value)):
                    with self.assertRaises(EvidenceLedgerError):
                        validate(changed)


if __name__ == "__main__":
    unittest.main()
