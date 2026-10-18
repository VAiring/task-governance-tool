"""Explicit, disposable review state and operations for schema-29/30/31 tests."""
from __future__ import annotations

import json
import tempfile
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from tests.verification_receipt_test_support import (
    initialize, add_task, set_target, target_for, run_taskgov, payload,
    add_receipt, completion,
)
from tests.test_task_contracts import edit_contract
from tests.test_review_session_migration import candidate_runtime
from tests.test_review_results import document, receipt, encode
from tests.test_review_continuation import A, B, ident, basis
from tests.test_review_answer_pure_values import metadata_fixture
from tests.test_m23s_schema22_validation import _bundle_artifacts
from task_governance_tool import storage, review_results, reviews
from task_governance_tool import review_session_transport as transport
from task_governance_tool import review_continuation as continuation
from task_governance_tool import review_continuation_repository as repository
from task_governance_tool import review_continuation_evidence as frozen
from task_governance_tool import review_answer_values as values
from task_governance_tool import schema_review_continuation as schema29
from task_governance_tool import schema_review_answers as schema30
from task_governance_tool import schema_criterion_link_collector as schema31
from task_governance_tool.review_continuation_evidence_repository import freeze_basis_locked
from task_governance_tool.review_answer import decode_answer
from task_governance_tool.review_answer_repository import CollectedReceiptInput, record_dispatch_locked
from task_governance_tool.review_session_repository import ReviewSessionBinding
from task_governance_tool.session_identity import CallerIdentity
from task_governance_tool.task_ownership import read_basis


@dataclass(frozen=True)
class ReviewContract:
    scope: str
    acceptance: str
    authority_ref: str


@dataclass
class ReviewFixture:
    repo: Path
    db: Path
    task: dict
    task_id: str
    target: object
    caller: CallerIdentity
    basis: dict
    packet: dict
    schema_version: int

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
                route="manual", selected=selected or (continuation.SelectedReviewer(actor, "reassess" if prior else "supplement", prior, "Explicit result continuation"),), caller=self.caller)
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

    def native_continuation_artifact(self):
        """Complete the real schema-29 scenario, retaining its version assertions."""
        old, finding, successor = self.reassess_with_finding()
        self.resolve(finding, successor=successor)
        recorded = run_taskgov("verification", "receipt", "add", self.task_id,
            "--result", "pass", "--duration-ms", "1", "--scope-coverage", "full",
            "--expected-target-generation", str(self.basis["target_generation"]),
            "--repo", str(self.repo), "--db", str(self.db), "--json")
        if recorded.returncode != 0:
            raise AssertionError(recorded.stdout + recorded.stderr)
        result = completion(self.db, self.repo, self.task_id)
        if result.returncode != 0 or not payload(result)["ok"]:
            raise AssertionError(result.stdout + result.stderr)
        with closing(storage.connect_initialized(self.target)) as connection:
            schema29.validate_storage(connection)
            bundle = connection.execute("SELECT * FROM completion_evidence_bundles WHERE task_id=?", (self.task_id,)).fetchone()
            if bundle["bundle_version"] != 3 or bundle["continuation_basis_json"] is None:
                raise AssertionError("schema-29 completion must seal a format-three continuation Bundle")
            graph = frozen.decode_graph_json(bundle["continuation_basis_json"])
            if successor not in graph["effective_receipt_ids"] or old in graph["effective_receipt_ids"]:
                raise AssertionError("frozen continuation must select the successor instead of its predecessor")
            _, artifacts = _bundle_artifacts(connection, self.basis["project_id"])
            artifact = artifacts[bundle["completion_evidence_bundle_id"]]
            if artifact.payload["review_continuation_basis"] != graph:
                raise AssertionError("projected Bundle must retain the exact frozen continuation graph")
            from tests.evidence_reader_oracle import ValidatedEvidenceSource
            entry = dict(task_id=self.task_id, completion_cycle_id=bundle["completion_cycle_id"],
                cycle_ordinal=bundle["cycle_ordinal"], bundle_state="native", bundle_id=bundle["completion_evidence_bundle_id"],
                bundle_file="bundles/" + bundle["completion_evidence_bundle_id"] + ".json", bundle_digest=artifact.bundle_digest,
                file_digest=artifact.file_digest, sealed_at=bundle["sealed_at"], bundle_format_version=3)
            source_basis = dict(index_format_version=2, source_schema_version=29, project_id=self.basis["project_id"],
                projection_generation=1, index_digest="sha256:" + "0" * 64, entry=entry)
            source = ValidatedEvidenceSource("native_bundle", source_basis, artifact.envelope)
            if source.source["payload"]["review_continuation_basis"] != graph:
                raise AssertionError("independent Reader must retain the exact frozen continuation graph")
            return artifact, source_basis

    def metadata(self, index=1, verdict="pass", findings=None):
        metadata, _ = metadata_fixture()
        metadata["basis"].update(execution_basis=self.basis, route="manual")
        metadata["binding"].update(basis_digest=values.basis_digest(metadata["basis"]), slot=index,
            reviewer_key="reviewer-" + str(index), parent_session_id=self.basis["parent_session_id"],
            reviewer_session_id=f"10000000-0000-4000-8000-{index:012x}")
        metadata["source_identity"].update(reviewer_session_id=metadata["binding"]["reviewer_session_id"], message_id="message-" + str(index))
        answer = decode_answer(json.dumps(dict(verdict=verdict, summary="review complete", findings=findings or [], checks=["source inspection"], limitations=[])).encode())
        metadata["original"].update(byte_length=len(answer.raw), digest=answer.original_digest, payload_start=answer.payload_start, payload_length=answer.payload_length, payload_digest=answer.payload_digest)
        return metadata, answer

    def add(self, connection, index=1, verdict="pass"):
        metadata, answer = self.metadata(index, verdict)
        submission_digest = "sha256:" + format(index + 11, "x") * 64
        result = reviews.add_review_receipt(connection, self.target.project, self.basis["task_id"],
            reviewer=metadata["binding"]["reviewer_key"], kind="independent", verdict=verdict, summary=answer.payload["summary"],
            caller=self.caller, database_target=self.target,
            session_binding=ReviewSessionBinding(metadata["source_identity"]["reviewer_session_id"], self.basis["execution_id"], "handoff", answer.original_digest.removeprefix("sha256:")),
            collected_answer=CollectedReceiptInput(metadata, answer, submission_digest))
        record_dispatch_locked(connection, preparation_id=metadata["basis"]["preparation_id"], submission_digest=submission_digest,
            project_id=self.basis["project_id"], task_id=self.basis["task_id"], entries=[self.entry(result.receipt, metadata, submission_digest)])
        return result.receipt, metadata

    def entry(self, receipt, metadata, submission_digest):
        host = receipt["review_provenance"]["host_observation"]
        return dict(slot=metadata["binding"]["slot"], protocol="review-answer-v1", receipt_id=receipt["review_receipt_id"], project_id=self.basis["project_id"], task_id=self.basis["task_id"], contract_revision=self.basis["contract_revision"], target={"kind":self.basis["target_kind"], "value":self.basis["target_value"], "base_revision":self.basis["target_base_revision"], "generation":self.basis["target_generation"], "capture_version":1}, source_preparation_id=metadata["basis"]["preparation_id"], source_continuation_id=None,
            reviewer_session_id=host["reviewer_session_id"], turn_id=host["turn_id"], message_id=host["message_id"], basis_digest=host["basis_digest"], original_digest=metadata["original"]["digest"], payload_digest=metadata["original"]["payload_digest"], submission_digest=submission_digest)

    def collected_source_continuation(self):
        metadata, answer = self.metadata(1)
        selected = (continuation.SelectedReviewer(metadata["binding"]["reviewer_session_id"], "supplement", None, "Inspect the source request"),)
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            source = repository.admit_locked(connection, basis=self.basis, preparation_id=metadata["basis"]["preparation_id"],
                route="manual", selected=selected, caller=self.caller)
            source = repository.advance_prepared_locked(connection, source, self.caller)
        metadata["basis"]["continuation"] = dict(continuation_id=source.continuation_id, sequence=source.sequence,
            selection_digest=continuation.selection_digest(source.selected))
        metadata["binding"]["basis_digest"] = values.basis_digest(metadata["basis"])
        return source, metadata, answer

    def retain_collected_source(self, predecessor, metadata):
        current, _ = self.metadata(2)
        selected = (continuation.SelectedReviewer(current["binding"]["reviewer_session_id"], "supplement", None, "Inspect the consumer request"),)
        cid = "tg_review_continuation_" + f"{predecessor.sequence + 1:016x}"
        capture = dict(kind="collected", consumer_continuation_id=cid, consumer_slot=2,
            source_preparation_id=metadata["basis"]["preparation_id"],
            source_continuation_id=metadata["basis"]["continuation"]["continuation_id"],
            source_slot=metadata["binding"]["slot"], session_id=metadata["source_identity"]["reviewer_session_id"],
            source_basis_digest=values.basis_digest(metadata["basis"]), original_digest=metadata["original"]["digest"],
            metadata_digest=values.metadata_digest(metadata))
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            consumer = repository.admit_locked(connection, basis=self.basis, preparation_id=metadata["basis"]["preparation_id"],
                route="manual", selected=selected, caller=self.caller, continuation_id=cid,
                retirement=continuation.RetirementProof(predecessor.continuation_id, continuation.basis_digest(self.basis), None, None),
                retained_collected=((capture, metadata),))
            consumer = repository.advance_prepared_locked(connection, consumer, self.caller)
        return consumer

    def register_collected_source(self):
        from task_governance_tool.review_answer_repository import read_native_collected_captures
        from task_governance_tool.review_answer_transport import CollectedItem, frame_collected_submission, decode_collected_submission
        from task_governance_tool.review_collected_results import add_collected_review_results
        source, metadata, answer = self.collected_source_continuation()
        consumer = self.retain_collected_source(source, metadata)
        consumer = self.retain_collected_source(consumer, metadata)
        current, current_answer = self.metadata(2)
        context = dict(continuation_id=consumer.continuation_id, sequence=consumer.sequence,
            selection_digest=continuation.selection_digest(consumer.selected))
        current["basis"]["continuation"] = context
        current["binding"].update(slot=1, basis_digest=values.basis_digest(current["basis"]))
        with closing(storage.connect_initialized(self.target)) as connection:
            captures = tuple(c for c, m in read_native_collected_captures(connection, consumer.continuation_id))
        raw = frame_collected_submission(metadata["basis"]["preparation_id"],
            (CollectedItem(1, current_answer.raw, current, current_answer), CollectedItem(2, answer.raw, metadata, answer)),
            continuation=context, retained_originals=captures)
        with closing(storage.connect_initialized(self.target)) as connection, connection:
            registered = add_collected_review_results(connection, self.target.project, self.basis["task_id"],
                decode_collected_submission(raw), caller=self.caller, database_target=self.target)
            if registered["review_gate"]["blocking_code"] is not None:
                raise AssertionError(registered["review_gate"])
        return consumer

    def collected_source_artifact(self):
        """Complete the real schema-30 collected continuation scenario."""
        self.register_collected_source()
        verified = add_receipt(self.db, self.repo, self.basis["task_id"], self.basis["target_generation"])
        if verified.returncode != 0:
            raise AssertionError(verified.stdout)
        completed = completion(self.db, self.repo, self.basis["task_id"])
        if completed.returncode != 0:
            raise AssertionError(completed.stdout)
        with closing(storage.connect_initialized(self.target)) as connection:
            schema30.validate_storage(connection)
            _, artifacts = _bundle_artifacts(connection, self.basis["project_id"])
            return next(iter(artifacts.values()))


def attach_existing(*, db, repo, task_id, schema_version, target=None):
    """Read existing state under the caller's runtime; never create or migrate it."""
    target = target if target is not None else target_for(db, repo)
    with closing(storage.connect_initialized_readonly(target)) as connection:
        if storage.current_schema_version(connection) != schema_version:
            raise AssertionError("existing fixture schema does not match its declared version")
        project_meta = connection.execute("SELECT canonical_path_hash,binding_generation FROM project_meta WHERE project_id=?",
            (target.project.project_id,)).fetchone()
        # Explicit-path test targets omit these observations; preserve the real
        # admitted metadata rather than relaxing the production basis contract.
        target = replace(target, binding_path_hash=project_meta["canonical_path_hash"],
            binding_generation=project_meta["binding_generation"])
        task = dict(connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())
        ownership = read_basis(connection, project_id=task["project_id"], task_id=task_id)
        caller = CallerIdentity(ownership.owner_session_id)
        captured_basis = basis(project_id=task["project_id"], task_id=task_id,
            contract_revision=task["current_contract_revision"], authority_snapshot_id=task["review_target_authority_snapshot_id"],
            acceptance_criterion_id=task["review_target_acceptance_criterion_id"], verification_criterion_id=task["review_target_verification_criterion_id"],
            artifact_manifest_id=task["review_target_artifact_manifest_id"],
            target_kind=task["review_target_kind"], target_value=task["review_target_value"],
            target_base_revision=task["review_target_base_revision"], target_generation=task["review_target_generation"],
            completion_commit_required=task["completion_commit_required"],
            project_path_hash=target.binding_path_hash, project_binding_generation=target.binding_generation,
            parent_session_id=ownership.owner_session_id, execution_id=ownership.execution_id, ownership_generation=ownership.generation)
    packet = dict(task={"task_id": task_id}, contract={"revision": captured_basis["contract_revision"]},
        review_target={key: captured_basis["target_" + key] for key in ("kind", "value", "base_revision", "generation")},
        review_session_context=dict(version=1, project_id=captured_basis["project_id"], execution_id=captured_basis["execution_id"]))
    return ReviewFixture(repo, db, task, task_id, target, caller, captured_basis, packet, schema_version)


@contextmanager
def review_fixture(*, schema_version, contract: ReviewContract | None = None):
    """Own temporary state and exactly one final runtime, including failed setup."""
    if type(schema_version) is not int or schema_version not in (29, 30, 31):
        raise ValueError("review fixture requires schema 29, 30, or 31")
    if contract is not None and not isinstance(contract, ReviewContract):
        raise TypeError("contract must be ReviewContract or None")
    with ExitStack() as stack:
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix="taskgov-review-fixture-"))
        with candidate_runtime(28):
            repo, db = initialize(Path(directory))
            task = add_task(db, repo, review_tier=2, status="ready" if contract else "in_progress")
            task_id = task["task_id"]
            if contract is not None:
                result, response = edit_contract(db, repo, task_id, contract.scope, contract.acceptance,
                    "--contract-authority-ref", contract.authority_ref, "--status", "in_progress")
                if result.returncode != 0:
                    raise AssertionError(response)
            set_target(db, repo, task_id)
        for version, module in ((29, schema29), (30, schema30), (31, schema31)):
            if version > schema_version:
                break
            with candidate_runtime(version), closing(storage.connect(db)) as connection:
                module.migrate(connection)
        stack.enter_context(candidate_runtime(schema_version))
        yield attach_existing(db=db, repo=repo, task_id=task_id, schema_version=schema_version)
