"""Original collected Finding sets survive every supported later write."""
from contextlib import closing
import io
import json
import sqlite3
import sys
from unittest import TestCase, mock

from tests.review_answer_fixture_support import review_fixture, ReviewContract
from tests.verification_receipt_test_support import run_taskgov, payload, add_receipt, completion
from tests.test_review_continuation import A, B
from task_governance_tool import storage, schema_viewer_projection, reviews
from task_governance_tool import review_answer_repository, review_collected_results
from task_governance_tool.review_answer_transport import (
    CollectedItem, frame_collected_submission, decode_collected_submission,
)
from task_governance_tool.review_continuation import selection_digest
from task_governance_tool.review_answer_values import basis_digest


def state(fixture):
    # Only this disposable fixture; include every row and trigger in comparison.
    with closing(sqlite3.connect(fixture.db)) as connection:
        return tuple(connection.iterdump())


def cli(fixture, *args):
    result = run_taskgov(*args, "--repo", str(fixture.repo), "--db", str(fixture.db), "--json")
    return result.returncode, payload(result)


def submission(fixture, count=0):
    items = []
    for slot in (1, 2):
        findings = [dict(severity="low", summary=f"Original issue {i}")
                    for i in range(count)] if slot == 1 else []
        metadata, answer = fixture.metadata(slot, findings=findings)
        items.append(CollectedItem(slot, answer.raw, metadata, answer))
    return decode_collected_submission(frame_collected_submission(
        items[0].metadata["basis"]["preparation_id"], items))


def register(fixture, submitted):
    with closing(storage.connect_initialized(fixture.target)) as connection, connection:
        return review_collected_results.add_collected_review_results(connection,
            fixture.target.project, fixture.task_id, submitted,
            caller=fixture.caller, database_target=fixture.target)


class CollectedFindingMutationTests(TestCase):
    def healthy(self, fixture):
        with closing(storage.connect_initialized_readonly(fixture.target)) as connection:
            schema_viewer_projection.validate_storage(connection)
        code, result = cli(fixture, "task", "show", fixture.task_id)
        self.assertEqual(code, 0, result)

    def rejected_add(self, fixture, receipt_id, summary="Additional issue"):
        before = state(fixture)
        for _ in range(2):
            code, result = cli(fixture, "review", "finding", "add", fixture.task_id,
                "--receipt-id", receipt_id, "--severity", "low", "--summary", summary)
            self.assertEqual(code, 1, result)
            self.assertEqual(result["errors"][0]["code"], "invalid_review_evidence")
            self.assertEqual(set(result["errors"][0]), {"code", "message"})
            self.assertEqual(state(fixture), before)
            self.healthy(fixture)

    def test_original_counts_criteria_and_legacy_controls(self):
        for collected in (True, False):
            for count in (0, 1, 2):
                for criteria in (False, True):
                    with self.subTest(collected=collected, count=count, criteria=criteria):
                        contract = ReviewContract("Inspect source", "Source reviewed", "AGENTS.md") if criteria else None
                        with review_fixture(schema_version=34, contract=contract) as f:
                            original = submission(f, count)
                            saved = (register(f, original) if collected else f.register([
                                f.original(A, findings=original.items[0].answer.payload["findings"]),
                                f.original(B)]))
                            rid = saved["receipts"][0]["receipt"]["review_receipt_id"]
                            self.healthy(f)
                            if collected:
                                self.rejected_add(f, rid)
                                if count:
                                    self.rejected_add(f, rid, "Original issue 0")
                            else:
                                code, result = cli(f, "review", "finding", "add", f.task_id,
                                    "--receipt-id", rid, "--severity", "low", "--summary", "Additional issue")
                                self.assertEqual(code, 0, result)
                                self.healthy(f)
                            code, result = cli(f, "review", "target", "set", f.task_id,
                                "--kind", "diff_fingerprint", "--revision", "sha256:" + "b" * 64)
                            self.assertEqual(code, 0, result)
                            self.healthy(f)

    def test_carried_and_new_selected_receipts_reject_additions(self):
        with review_fixture(schema_version=34) as f:
            consumer = f.register_collected_source()
            with closing(storage.connect_initialized_readonly(f.target)) as connection:
                ids = [connection.execute(query, (consumer.continuation_id,)).fetchone()[0] for query in (
                    "SELECT result_receipt_id FROM review_collected_captures WHERE continuation_id=? AND result_receipt_id IS NOT NULL",
                    "SELECT result_receipt_id FROM review_continuation_reviewers WHERE continuation_id=? AND result_receipt_id IS NOT NULL")]
            self.assertEqual(len(set(ids)), 2)
            for rid in ids:
                self.rejected_add(f, rid)

    def test_initial_batch_rejects_nonoriginal_and_duplicate_prefix_atomically(self):
        for changed in ("unknown", "duplicate"):
            with self.subTest(changed=changed), review_fixture(schema_version=34) as f:
                submitted = submission(f, 2)
                before = state(f)
                actual = review_collected_results.add_review_finding
                seen = []
                def append(*args, **kwargs):
                    if seen:
                        kwargs["summary"] = "Original issue 0" if changed == "duplicate" else "Not in original"
                    seen.append(True)
                    return actual(*args, **kwargs)
                with mock.patch.object(review_collected_results, "add_review_finding", side_effect=append):
                    with self.assertRaises(reviews.ReviewEvidenceError) as failure:
                        register(f, submitted)
                self.assertEqual(failure.exception.code, "invalid_review_evidence")
                self.assertEqual(len(seen), 2)
                self.assertEqual(state(f), before)
                register(f, submitted)
                self.healthy(f)

    def test_material_sharing_batch_does_not_authorize_registered_append(self):
        from task_governance_tool.review_material_repository import finding_batch
        with review_fixture(schema_version=34) as f:
            saved = register(f, submission(f, 1))
            rid = saved["receipts"][0]["receipt"]["review_receipt_id"]
            before = state(f)
            with closing(storage.connect_initialized(f.target)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                task = dict(connection.execute("SELECT * FROM tasks WHERE task_id=?", (f.task_id,)).fetchone())
                with finding_batch(connection, f.target.project, task) as batch:
                    with self.assertRaises(reviews.ReviewEvidenceError):
                        reviews.add_review_finding(connection, f.target.project, f.task_id,
                            receipt_id=rid, severity="low", summary="Additional issue",
                            database_target=f.target, caller=f.caller, _batch=batch)
            self.assertEqual(state(f), before)
            self.healthy(f)

    def test_grouped_resolution_rollback_and_immutable_originals(self):
        with review_fixture(schema_version=34) as f:
            saved = register(f, submission(f, 2))
            ids = [row["finding"]["review_finding_id"] for row in saved["receipts"][0]["findings"]]
            def facts():
                with closing(storage.connect_initialized_readonly(f.target)) as connection:
                    result = {table: tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                        for table in ("review_receipts", "review_receipt_provenance_v2", "review_collection_sources",
                            "review_collection_dispatches", "review_collection_dispatch_entries", "evidence_references")}
                    result["originals"] = tuple(tuple(row) for row in connection.execute(
                        "SELECT review_finding_id,review_receipt_id,severity,summary,created_at FROM review_findings ORDER BY rowid"))
                    return result
            def resolve(selected):
                raw = json.dumps(dict(version=1, task_id=f.task_id,
                    resolutions=[dict(finding_ids=selected, resolution="Explicit repair")])).encode()
                with mock.patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")):
                    return cli(f, "review", "finding", "resolve", "--from-stdin")
            before, originals = state(f), facts()
            code, result = resolve([ids[0], "tg_review_finding_" + "0" * 16])
            self.assertEqual(code, 1, result)
            self.assertEqual(result["data"]["findings"], [])
            self.assertEqual(state(f), before)
            self.assertEqual(resolve(ids)[0], 0)
            self.assertEqual(facts(), originals)
            after = state(f)
            self.assertEqual(resolve(ids)[0], 1)
            self.assertEqual(state(f), after)
            self.healthy(f)

    def test_existing_corruption_stays_detected_and_is_not_implicitly_repaired(self):
        with review_fixture(schema_version=34) as f:
            rid = register(f, submission(f))["receipts"][0]["receipt"]["review_receipt_id"]
            # Reproduce the old successful writer on a disposable DB, not raw repair SQL.
            with mock.patch.object(review_answer_repository, "require_initial_payload_finding"):
                code, result = cli(f, "review", "finding", "add", f.task_id,
                    "--receipt-id", rid, "--severity", "low", "--summary", "Preexisting extra issue")
                self.assertEqual(code, 0, result)
            fid = result["data"]["finding"]["review_finding_id"]
            before = state(f)
            for args in (("task", "show", f.task_id),
                         ("review", "finding", "resolve", fid, "--resolution", "Explicit repair"),
                         ("review", "target", "set", f.task_id, "--kind", "diff_fingerprint", "--revision", "sha256:" + "b" * 64)):
                code, result = cli(f, *args)
                self.assertEqual(code, 2, result)
                self.assertEqual(result["errors"][0]["code"], "evidence_ledger_inconsistent")
                self.assertEqual(state(f), before)
            with closing(storage.connect(f.db)) as connection:
                with self.assertRaises(storage.StorageError):
                    schema_viewer_projection.validate_storage(connection)

    def test_reassessment_keeps_both_original_sets_and_explicit_resolution_links(self):
        for timing in ("before", "with", "after"):
            with self.subTest(timing=timing), review_fixture(schema_version=34) as f:
                initial = submission(f, 1)
                saved = register(f, initial)
                old = saved["receipts"][0]["receipt"]["review_receipt_id"]
                fid = saved["receipts"][0]["findings"][0]["finding"]["review_finding_id"]
                retained = saved["receipts"][1]["receipt"]["review_receipt_id"]
                if timing == "before":
                    f.resolve(fid, summary="Original resolution")
                continued = f.prepare(actor=initial.items[0].metadata["binding"]["reviewer_session_id"], prior=old)
                metadata, answer = f.metadata(1)
                context = dict(continuation_id=continued.continuation_id, sequence=continued.sequence,
                    selection_digest=selection_digest(continued.selected))
                metadata["basis"]["continuation"] = context
                metadata["binding"].update(basis_digest=basis_digest(metadata["basis"]),
                    initial_turn_id="20000000-0000-4000-8000-000000000001")
                metadata["source_identity"].update(turn_id=metadata["binding"]["initial_turn_id"], message_id="successor-message")
                following = decode_collected_submission(frame_collected_submission(
                    metadata["basis"]["preparation_id"], (CollectedItem(1, answer.raw, metadata, answer),),
                    continuation=context, retained_receipt_ids=(retained,)))
                successor = register(f, following)["receipts"][0]["receipt"]["review_receipt_id"]
                self.rejected_add(f, old)
                self.rejected_add(f, successor)
                if timing == "after":
                    f.resolve(fid, summary="Original resolution")
                f.resolve(fid, successor=successor, summary="Explicit reassessment confirmation")
                self.healthy(f)
                with closing(storage.connect_initialized_readonly(f.target)) as connection:
                    row = connection.execute("SELECT severity,summary,status,resolution_summary FROM review_findings WHERE review_finding_id=?", (fid,)).fetchone()
                    self.assertEqual(tuple(row)[:3], ("low", "Original issue 0", "resolved"))
                    self.assertEqual(row[3], "Explicit reassessment confirmation" if timing == "with" else "Original resolution")
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_reassessment_resolutions").fetchone()[0], 1)

    def test_sealed_bundle_and_reopened_history_keep_original_bytes(self):
        from tests.test_m23s_schema22_validation import _bundle_artifacts
        with review_fixture(schema_version=34) as f:
            rid = register(f, submission(f, 1))["receipts"][0]["receipt"]["review_receipt_id"]
            self.rejected_add(f, rid)
            verified = add_receipt(f.db, f.repo, f.task_id, f.basis["target_generation"])
            self.assertEqual(verified.returncode, 0, verified.stdout)
            completed = completion(f.db, f.repo, f.task_id)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            def bundle_rows():
                with closing(storage.connect_initialized_readonly(f.target)) as connection:
                    schema_viewer_projection.validate_storage(connection)
                    _bundle_artifacts(connection, f.basis["project_id"])
                    return tuple(tuple(row) for row in connection.execute("SELECT * FROM completion_evidence_bundles ORDER BY rowid"))
            original = bundle_rows()
            self.assertEqual(len(original), 1)
            before = state(f)
            self.assertEqual(cli(f, "review", "finding", "add", f.task_id,
                "--receipt-id", rid, "--severity", "low", "--summary", "After completion")[0], 1)
            self.assertEqual(state(f), before)
            code, result = cli(f, "task", "edit", f.task_id, "--status", "in_progress",
                "--reopen-reason", "Inspect a later target")
            self.assertEqual(code, 0, result)
            code, result = cli(f, "review", "target", "set", f.task_id,
                "--kind", "diff_fingerprint", "--revision", "sha256:" + "b" * 64)
            self.assertEqual(code, 0, result)
            self.assertEqual(bundle_rows(), original)
            self.healthy(f)
