"""Isolated schema-30 evidence, registration identity and shared-gate checks."""
from __future__ import annotations
import copy
import json
import sqlite3
import unittest
from unittest import mock
from contextlib import closing

from tests import test_review_continuation_registration as continuation_fixture
from tests.test_review_session_migration import candidate_runtime
from tests.test_review_answer_pure_values import metadata_fixture
from task_governance_tool import storage, reviews
from task_governance_tool import schema_review_answers as schema
from task_governance_tool import review_answer_values as values
from task_governance_tool.review_answer import decode_answer
from task_governance_tool.review_answer_repository import CollectedReceiptInput, record_dispatch_locked, read_registration
from task_governance_tool.review_session_repository import ReviewSessionBinding
from task_governance_tool.task_values import TaskValidationError
from tests.verification_receipt_test_support import add_receipt, completion, set_target
from tests.test_m23s_schema22_validation import _bundle_artifacts
from tests.test_m23s_schema22_migration import _logical_snapshot, _add_unrelated_objects


class CollectedCoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture=continuation_fixture.ContinuationRegistrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.enterContext(candidate_runtime(30))
        self.db,self.target,self.basis,self.caller=self.fixture.db,self.fixture.target,self.fixture.basis,self.fixture.caller
        with closing(storage.connect(self.db)) as connection:
            schema.migrate(connection)

    def metadata(self,index=1,verdict="pass",findings=None):
        metadata,_=metadata_fixture()
        metadata["basis"].update(execution_basis=self.basis,route="manual")
        metadata["binding"].update(basis_digest=values.basis_digest(metadata["basis"]),slot=index,
            reviewer_key="reviewer-"+str(index),parent_session_id=self.basis["parent_session_id"],
            reviewer_session_id=f"10000000-0000-4000-8000-{index:012x}")
        metadata["source_identity"].update(reviewer_session_id=metadata["binding"]["reviewer_session_id"],message_id="message-"+str(index))
        answer=decode_answer(json.dumps(dict(verdict=verdict,summary="review complete",findings=findings or [],checks=["source inspection"],limitations=[])).encode())
        metadata["original"].update(byte_length=len(answer.raw),digest=answer.original_digest,payload_start=answer.payload_start,payload_length=answer.payload_length,payload_digest=answer.payload_digest)
        return metadata,answer

    def add(self,connection,index=1,verdict="pass"):
        metadata,answer=self.metadata(index,verdict)
        submission_digest="sha256:"+format(index+11,"x")*64
        result=reviews.add_review_receipt(connection,self.target.project,self.basis["task_id"],
            reviewer=metadata["binding"]["reviewer_key"],kind="independent",verdict=verdict,summary=answer.payload["summary"],
            caller=self.caller,database_target=self.target,
            session_binding=ReviewSessionBinding(metadata["source_identity"]["reviewer_session_id"],self.basis["execution_id"],"handoff",answer.original_digest.removeprefix("sha256:")),
            collected_answer=CollectedReceiptInput(metadata,answer,submission_digest))
        record_dispatch_locked(connection,preparation_id=metadata["basis"]["preparation_id"],submission_digest=submission_digest,
            project_id=self.basis["project_id"],task_id=self.basis["task_id"],entries=[self.entry(result.receipt,metadata,submission_digest)])
        return result.receipt,metadata

    def entry(self,receipt,metadata,submission_digest):
        host=receipt["review_provenance"]["host_observation"]
        return dict(slot=metadata["binding"]["slot"],protocol="review-answer-v1",receipt_id=receipt["review_receipt_id"],project_id=self.basis["project_id"],task_id=self.basis["task_id"],contract_revision=self.basis["contract_revision"],target={"kind":self.basis["target_kind"],"value":self.basis["target_value"],"base_revision":self.basis["target_base_revision"],"generation":self.basis["target_generation"],"capture_version":1},source_preparation_id=metadata["basis"]["preparation_id"],source_continuation_id=None,
            reviewer_session_id=host["reviewer_session_id"],turn_id=host["turn_id"],message_id=host["message_id"],basis_digest=host["basis_digest"],original_digest=metadata["original"]["digest"],payload_digest=metadata["original"]["payload_digest"],submission_digest=submission_digest)

    def collected_source_continuation(self):
        from task_governance_tool import review_continuation as cv, review_continuation_repository as cr
        metadata,answer=self.metadata(1)
        selected=(cv.SelectedReviewer(metadata["binding"]["reviewer_session_id"],"supplement",None,"Inspect the source request"),)
        with closing(storage.connect_initialized(self.target)) as connection,connection:
            connection.execute("BEGIN IMMEDIATE")
            source=cr.admit_locked(connection,basis=self.basis,preparation_id=metadata["basis"]["preparation_id"],
                route="manual",selected=selected,caller=self.caller)
            source=cr.advance_prepared_locked(connection,source,self.caller)
        metadata["basis"]["continuation"]=dict(continuation_id=source.continuation_id,sequence=source.sequence,
            selection_digest=cv.selection_digest(source.selected))
        metadata["binding"]["basis_digest"]=values.basis_digest(metadata["basis"])
        return source,metadata,answer

    def retain_collected_source(self, predecessor, metadata):
        from task_governance_tool import review_continuation as cv, review_continuation_repository as cr
        current,_=self.metadata(2)
        selected=(cv.SelectedReviewer(current["binding"]["reviewer_session_id"],"supplement",None,"Inspect the consumer request"),)
        cid="tg_review_continuation_"+f"{predecessor.sequence+1:016x}"
        capture=dict(kind="collected",consumer_continuation_id=cid,consumer_slot=2,
            source_preparation_id=metadata["basis"]["preparation_id"],
            source_continuation_id=metadata["basis"]["continuation"]["continuation_id"],
            source_slot=metadata["binding"]["slot"],session_id=metadata["source_identity"]["reviewer_session_id"],
            source_basis_digest=values.basis_digest(metadata["basis"]),original_digest=metadata["original"]["digest"],
            metadata_digest=values.metadata_digest(metadata))
        with closing(storage.connect_initialized(self.target)) as connection,connection:
            connection.execute("BEGIN IMMEDIATE")
            consumer=cr.admit_locked(connection,basis=self.basis,preparation_id=metadata["basis"]["preparation_id"],
                route="manual",selected=selected,caller=self.caller,continuation_id=cid,
                retirement=cv.RetirementProof(predecessor.continuation_id,cv.basis_digest(self.basis),None,None),
                retained_collected=((capture,metadata),))
            consumer=cr.advance_prepared_locked(connection,consumer,self.caller)
        return consumer

    def register_collected_source(self):
        from task_governance_tool import review_continuation as cv
        from task_governance_tool.review_answer_repository import read_native_collected_captures
        from task_governance_tool.review_answer_transport import CollectedItem, frame_collected_submission, decode_collected_submission
        from task_governance_tool.review_collected_results import add_collected_review_results
        source,metadata,answer=self.collected_source_continuation()
        consumer=self.retain_collected_source(source,metadata)
        consumer=self.retain_collected_source(consumer,metadata)
        current,current_answer=self.metadata(2)
        context=dict(continuation_id=consumer.continuation_id,sequence=consumer.sequence,
            selection_digest=cv.selection_digest(consumer.selected))
        current["basis"]["continuation"]=context
        current["binding"].update(slot=1,basis_digest=values.basis_digest(current["basis"]))
        with closing(storage.connect_initialized(self.target)) as connection:
            captures=tuple(c for c,m in read_native_collected_captures(connection,consumer.continuation_id))
        raw=frame_collected_submission(metadata["basis"]["preparation_id"],
            (CollectedItem(1,current_answer.raw,current,current_answer),CollectedItem(2,answer.raw,metadata,answer)),
            continuation=context,retained_originals=captures)
        with closing(storage.connect_initialized(self.target)) as connection,connection:
            registered=add_collected_review_results(connection,self.target.project,self.basis["task_id"],
                decode_collected_submission(raw),caller=self.caller,database_target=self.target)
            self.assertIsNone(registered["review_gate"]["blocking_code"])
        return consumer

    def collected_source_artifact(self):
        self.register_collected_source()
        verified=add_receipt(self.db,self.fixture.repo,self.basis["task_id"],self.basis["target_generation"])
        self.assertEqual(verified.returncode,0,verified.stdout)
        completed=completion(self.db,self.fixture.repo,self.basis["task_id"])
        self.assertEqual(completed.returncode,0,completed.stdout)
        with closing(storage.connect_initialized(self.target)) as connection:
            schema.validate_storage(connection)
            _,artifacts=_bundle_artifacts(connection,self.basis["project_id"])
            return next(iter(artifacts.values()))

    def test_capture_admission_binds_source_sequence_and_selection_with_recomputed_seals(self):
        from task_governance_tool import review_continuation_repository as cr
        source,metadata,_=self.collected_source_continuation()
        for field,value in (("sequence",source.sequence+1),("selection_digest","f"*64)):
            changed=copy.deepcopy(metadata)
            changed["basis"]["continuation"][field]=value
            changed["binding"]["basis_digest"]=values.basis_digest(changed["basis"])
            with self.subTest(field=field):
                with self.assertRaises(storage.StorageError) as rejected:
                    self.retain_collected_source(source,changed)
                self.assertEqual(rejected.exception.code,"evidence_ledger_inconsistent")
                with closing(storage.connect_initialized(self.target)) as connection:
                    self.assertEqual(cr.read_continuations(connection,project_id=self.basis["project_id"],task_id=self.basis["task_id"]),(source,))
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_collected_captures").fetchone()[0],0)
        self.retain_collected_source(source,metadata)

    def test_schema_reentry_rejects_resealed_source_sequence_and_selection(self):
        from task_governance_tool import review_continuation_repository as cr
        source,metadata,_=self.collected_source_continuation()
        consumer=self.retain_collected_source(source,metadata)
        with closing(storage.connect_initialized(self.target)) as original:
            schema.validate_storage(original)
            for field,value in (("sequence",source.sequence+1),("selection_digest","f"*64)):
                with self.subTest(field=field),closing(sqlite3.connect(":memory:")) as connection:
                    connection.row_factory=sqlite3.Row
                    connection.execute("PRAGMA foreign_keys=ON")
                    original.backup(connection)
                    row=connection.execute("SELECT * FROM review_collected_captures WHERE continuation_id=?",(consumer.continuation_id,)).fetchone()
                    changed=json.loads(row["metadata_json"])
                    changed["basis"]["continuation"][field]=value
                    changed["binding"]["basis_digest"]=values.basis_digest(changed["basis"])
                    projection=json.loads(row["projection_json"])
                    projection["source_metadata"]=values.source_metadata_projection(changed)
                    projection["capture_identity"]["source_basis_digest"]=values.basis_digest(changed["basis"])
                    projection["evidence_digest"]=values.capture_evidence_digest(projection["capture_identity"],projection["source_metadata"])
                    trigger=connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_review_collected_captures_publish'").fetchone()[0]
                    connection.execute("DROP TRIGGER trg_review_collected_captures_publish")
                    connection.execute("UPDATE review_collected_captures SET metadata_json=?,metadata_digest=?,projection_json=? WHERE continuation_id=?",
                        (json.dumps(changed,sort_keys=True,separators=(',',':')),values.metadata_digest(changed),
                         json.dumps(projection,sort_keys=True,separators=(',',':')),consumer.continuation_id))
                    connection.execute(trigger)
                    connection.commit()
                    schema.validate_owned_contract(connection)
                    before=_logical_snapshot(connection)
                    with self.assertRaises(storage.StorageError) as rejected:
                        cr.read_continuations(connection,project_id=self.basis["project_id"],task_id=self.basis["task_id"])
                    self.assertEqual(rejected.exception.code,"evidence_ledger_inconsistent")
                    with self.assertRaises(storage.StorageError) as rejected:
                        schema.migrate(connection)
                    self.assertEqual(rejected.exception.code,"evidence_ledger_inconsistent")
                    self.assertEqual(_logical_snapshot(connection),before)

    def test_resealed_capture_reviewer_key_rejected_by_native_read_and_readonly_reentry(self):
        from task_governance_tool import review_continuation_repository as cr
        self.register_collected_source()
        with closing(storage.connect_initialized(self.target)) as original:
            self.assertEqual(original.execute("SELECT COUNT(*) FROM completion_evidence_bundles").fetchone()[0],0)
            valid=cr.read_continuations(original,project_id=self.basis["project_id"],task_id=self.basis["task_id"])
            self.assertEqual([row.state for row in valid],["superseded","superseded","registered"])
            before=_logical_snapshot(original)
            original.execute("PRAGMA query_only=ON")
            self.assertFalse(schema.migrate(original))
            self.assertEqual(_logical_snapshot(original),before)
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.row_factory=sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                original.backup(connection)
                rows=connection.execute("SELECT * FROM review_collected_captures ORDER BY continuation_id,session_id").fetchall()
                self.assertEqual(len(rows),2)
                self.assertEqual(sum(row["result_receipt_id"] is not None for row in rows),1)
                trigger=connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_review_collected_captures_publish'").fetchone()[0]
                connection.execute("DROP TRIGGER trg_review_collected_captures_publish")
                # Change every transfer consistently, including historical null links,
                # so only the linked Receipt identity exposes the corruption.
                for row in rows:
                    metadata=json.loads(row["metadata_json"])
                    metadata["binding"]["reviewer_key"]="different-reviewer"
                    self.assertEqual(values.validate_metadata(metadata),metadata)
                    projection=json.loads(row["projection_json"])
                    projection["source_metadata"]=values.source_metadata_projection(metadata)
                    projection["evidence_digest"]=values.capture_evidence_digest(projection["capture_identity"],projection["source_metadata"])
                    self.assertEqual(values.validate_capture_evidence(projection),projection)
                    connection.execute("UPDATE review_collected_captures SET metadata_json=?,metadata_digest=?,projection_json=? WHERE continuation_id=? AND session_id=?",
                        (json.dumps(metadata,sort_keys=True,separators=(',',':')),values.metadata_digest(metadata),
                         json.dumps(projection,sort_keys=True,separators=(',',':')),row["continuation_id"],row["session_id"]))
                connection.execute(trigger)
                connection.commit()
                schema.validate_owned_contract(connection)
                before=_logical_snapshot(connection)
                connection.execute("PRAGMA query_only=ON")
                with self.assertRaises(storage.StorageError) as rejected:
                    cr.read_continuations(connection,project_id=self.basis["project_id"],task_id=self.basis["task_id"])
                self.assertEqual(rejected.exception.code,"evidence_ledger_inconsistent")
                with self.assertRaises(storage.StorageError) as rejected:
                    schema.migrate(connection)
                self.assertEqual(rejected.exception.code,"evidence_ledger_inconsistent")
                self.assertFalse(connection.in_transaction)
                self.assertEqual(_logical_snapshot(connection),before)

    def test_v2_roundtrip_and_unable_blocks_two_passes(self):
        with closing(storage.connect_initialized(self.target)) as connection:
            for index,verdict in ((1,"pass"),(2,"pass"),(3,"unable_to_review")):
                receipt,_=self.add(connection,index,verdict)
                self.assertEqual(receipt["review_provenance"]["provenance_version"],2)
                connection.commit()
            schema.validate_storage(connection)
            evidence=reviews.read_review_evidence(connection,self.basis["project_id"],self.basis["task_id"],review_tier=2)
            self.assertEqual(evidence["gate"]["qualifying_independent_passes"],2)
            self.assertFalse(evidence["gate"]["satisfied"])
            self.assertEqual(reviews.first_review_gate_error(evidence).code,"review_unable")
            self.assertEqual(evidence["counts"]["unable_current_generation"],1)
        from task_governance_tool.viewer import build_viewer_snapshot
        with closing(storage.connect_initialized_readonly(self.target)) as connection:
            snapshot=build_viewer_snapshot(connection,self.target).snapshot
            self.assertEqual(snapshot["snapshot_version"],4)
            shown=next(t for t in snapshot["tasks"] if t["task_id"]==self.basis["task_id"])
            self.assertFalse(shown["review_evidence"]["gate"]["satisfied"])
            self.assertEqual(shown["review_evidence"]["counts"]["unable_current_generation"],1)
            self.assertTrue(all("review_provenance" not in r for r in shown["review_evidence"]["recent_receipts"]))

    def test_resealed_v2_provenance_id_mismatch_rejects_readonly_reentry(self):
        from task_governance_tool.evidence_ledger import (
            EvidenceSource, TargetCaptureBinding, build_evidence_reference,
            canonical_json_bytes,
        )
        from task_governance_tool.review_answer_provenance import validate_provenance_v2
        from task_governance_tool.review_answer_repository import (
            validate_provenance_relation, validate_storage_rows,
        )
        with closing(storage.connect_initialized(self.target)) as original:
            public_receipt,_=self.add(original)
            original.commit()
            receipt_id=public_receipt["review_receipt_id"]
            receipt=original.execute("SELECT * FROM review_receipts WHERE review_receipt_id=?",(receipt_id,)).fetchone()
            self.assertEqual(validate_provenance_relation(original,receipt),public_receipt["review_provenance"])
            before=_logical_snapshot(original)
            original.execute("PRAGMA query_only=ON")
            self.assertFalse(schema.migrate(original))
            self.assertEqual(_logical_snapshot(original),before)
            with closing(sqlite3.connect(":memory:")) as connection:
                connection.row_factory=sqlite3.Row
                connection.execute("PRAGMA foreign_keys=ON")
                original.backup(connection)
                row=connection.execute("SELECT * FROM review_receipt_provenance_v2 WHERE review_receipt_id=?",(receipt_id,)).fetchone()
                changed=json.loads(row["provenance_json"])
                changed["review_provenance_id"]="tg_review_provenance_"+("0"*16 if row["review_provenance_id"]!="tg_review_provenance_"+"0"*16 else "1"*16)
                # The random ID is outside the contextual digest by contract.
                # The pure value remains valid; only the native relation can
                # detect its mismatch with the unchanged Receipt and row ID.
                self.assertEqual(validate_provenance_v2(changed,
                    project_id=receipt["project_id"],task_id=receipt["task_id"],
                    review_receipt_id=receipt_id,receipt_kind=receipt["receipt_kind"],
                    target={**{key:receipt["target_"+key] for key in ("kind","value","base_revision","generation")},"capture_version":1}),changed)
                reference=connection.execute("SELECT * FROM evidence_references WHERE source_kind='review_receipt' AND source_id=?",(receipt_id,)).fetchone()
                projection={key:receipt[key] for key in ("review_receipt_id","reviewer_key","receipt_kind","verdict","summary","user_approved","created_at")}
                projection["review_provenance"]=changed
                resealed=build_evidence_reference(
                    source=EvidenceSource("review_receipt","recorded",receipt_id,projection),
                    project_id=reference["project_id"],task_id=reference["task_id"],
                    contract_revision=reference["contract_revision"],
                    binding=TargetCaptureBinding(**{key:reference[key] for key in ("target_kind","target_value","target_base_revision","target_generation","authority_snapshot_id","acceptance_criterion_id","verification_criterion_id")}))
                self.assertNotEqual(resealed.digest,reference["digest"])
                triggers=("trg_review_receipt_provenance_v2_no_update","trg_evidence_references_no_update")
                ddl=[connection.execute("SELECT sql FROM sqlite_master WHERE name=?",(name,)).fetchone()[0] for name in triggers]
                for name in triggers:connection.execute("DROP TRIGGER "+name)
                connection.execute("UPDATE review_receipt_provenance_v2 SET provenance_json=? WHERE review_receipt_id=?",(canonical_json_bytes(changed).decode(),receipt_id))
                connection.execute("UPDATE evidence_references SET digest=? WHERE evidence_reference_id=?",(resealed.digest,reference["evidence_reference_id"]))
                for sql in ddl:connection.execute(sql)
                connection.commit()
                schema.validate_owned_contract(connection)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(),[])
                before=_logical_snapshot(connection)
                connection.execute("PRAGMA query_only=ON")
                for boundary,read in (("native relation",lambda:validate_provenance_relation(connection,receipt)),
                    ("native inventory",lambda:validate_storage_rows(connection)),
                    ("schema reentry",lambda:schema.migrate(connection))):
                    with self.subTest(boundary=boundary):
                        with self.assertRaises(storage.StorageError) as rejected:
                            read()
                        error=rejected.exception
                        if error.code=="project_state_unreadable":error=error.__cause__
                        self.assertIsInstance(error,storage.StorageError)
                        self.assertEqual(error.code,"evidence_ledger_inconsistent")
                self.assertFalse(connection.in_transaction)
                self.assertEqual(_logical_snapshot(connection),before)

    def test_legacy_entry_cannot_write_unable(self):
        with closing(storage.connect_initialized(self.target)) as connection:
            with self.assertRaises((reviews.ReviewEvidenceError,TaskValidationError)):
                reviews.add_review_receipt(connection,self.target.project,self.basis["task_id"],reviewer="old",kind="independent",verdict="unable_to_review",caller=self.caller,database_target=self.target)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_receipts").fetchone()[0],0)

    def test_exact_inventory_reconciliation_survives_target_history(self):
        with closing(storage.connect_initialized(self.target)) as connection:
            receipt,metadata=self.add(connection)
            host=receipt["review_provenance"]["host_observation"]
            entry=dict(slot=1,protocol="review-answer-v1",receipt_id=receipt["review_receipt_id"],project_id=self.basis["project_id"],task_id=self.basis["task_id"],contract_revision=self.basis["contract_revision"],target={"kind":self.basis["target_kind"],"value":self.basis["target_value"],"base_revision":self.basis["target_base_revision"],"generation":self.basis["target_generation"],"capture_version":1},source_preparation_id=metadata["basis"]["preparation_id"],source_continuation_id=None,
                reviewer_session_id=host["reviewer_session_id"],turn_id=host["turn_id"],message_id=host["message_id"],basis_digest=host["basis_digest"],original_digest=metadata["original"]["digest"],payload_digest=metadata["original"]["payload_digest"],submission_digest="sha256:"+"c"*64)
            args=dict(preparation_id=metadata["basis"]["preparation_id"],submission_digest=entry["submission_digest"],project_id=self.basis["project_id"],task_id=self.basis["task_id"])
            record_dispatch_locked(connection,entries=[entry],**args)
            connection.commit()
            self.assertEqual(read_registration(connection,**args)["status"],"recorded")
            self.assertTrue(read_registration(connection,**args)["current_basis"])
            self.assertEqual(read_registration(connection,**{**args,"submission_digest":"sha256:"+"d"*64})["status"],"absent")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DROP TRIGGER trg_review_collection_dispatch_entries_no_delete")
            connection.execute("DELETE FROM review_collection_dispatch_entries")
            incomplete=read_registration(connection,**args)
            self.assertEqual((incomplete["status"],incomplete["reason"],incomplete["entries"]),("conflict","partial_inventory",[]))
            connection.rollback()
        set_target(self.db,self.fixture.repo,self.basis["task_id"],fingerprint="sha256:"+"e"*64)
        with closing(storage.connect_initialized(self.target)) as connection:
            historical=read_registration(connection,**args)
            self.assertEqual(historical["status"],"recorded")
            self.assertFalse(historical["current_basis"])

    def test_migration_reentry_preserves_old_tables_and_rejects_schema_drift(self):
        with closing(storage.connect(self.db)) as connection:
            self.assertFalse(schema.migrate(connection))
            connection.execute("CREATE INDEX answer_foreign_attachment ON review_receipts(summary)")
            with self.assertRaises(storage.StorageError):
                schema.validate_storage(connection)

    def test_collected_passes_seal_bundle_four_and_index_three(self):
        with closing(storage.connect_initialized(self.target)) as connection:
            self.add(connection,1)
            self.add(connection,2)
            connection.commit()
        verified=add_receipt(self.db,self.fixture.repo,self.basis["task_id"],self.basis["target_generation"])
        self.assertEqual(verified.returncode,0,verified.stdout)
        completed=completion(self.db,self.fixture.repo,self.basis["task_id"])
        self.assertEqual(completed.returncode,0,completed.stdout)
        with closing(storage.connect_initialized(self.target)) as connection:
            basis,bundles=_bundle_artifacts(connection,self.basis["project_id"])
            from task_governance_tool.evidence_projection import _render_projection
            self.assertEqual(_render_projection(basis).index.envelope["format_version"],3)
            self.assertEqual(len(bundles),1)
            envelope=next(iter(bundles.values())).envelope
            self.assertEqual(envelope["format_version"],4)
            self.assertIsNone(envelope["payload"]["review_continuation_basis"])

    def test_missing_source30_execution_cycle_link_rejects_admission_and_recovery(self):
        with closing(storage.connect_initialized(self.target)) as connection:
            self.add(connection,1)
            self.add(connection,2)
            connection.commit()
        verified=add_receipt(self.db,self.fixture.repo,self.basis["task_id"],self.basis["target_generation"])
        self.assertEqual(verified.returncode,0,verified.stdout)
        completed=completion(self.db,self.fixture.repo,self.basis["task_id"])
        self.assertEqual(completed.returncode,0,completed.stdout)
        with closing(storage.connect_initialized(self.target)) as connection:
            bundle=connection.execute("SELECT * FROM completion_evidence_bundles WHERE task_id=?",
                (self.basis["task_id"],)).fetchone()
            self.assertEqual(bundle["source_schema_version"],30)
            cycle_id=bundle["completion_cycle_id"]
            link=connection.execute("SELECT * FROM task_execution_cycles WHERE completion_cycle_id=?",
                (cycle_id,)).fetchone()
            self.assertEqual((link["project_id"],link["task_id"],link["execution_id"]),
                (self.basis["project_id"],self.basis["task_id"],self.basis["execution_id"]))
            schema.validate_storage(connection)
            schema.validate_storage(connection,recovery=True)
            # Keep the exact owned DDL and all remaining relations valid so
            # only the missing mandatory native-cycle link exposes corruption.
            trigger=connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_task_execution_cycles_no_delete'").fetchone()[0]
            connection.execute("DROP TRIGGER trg_task_execution_cycles_no_delete")
            connection.execute("DELETE FROM task_execution_cycles WHERE completion_cycle_id=?",(cycle_id,))
            connection.execute(trigger)
            connection.commit()
            schema.validate_owned_contract(connection)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0],"ok")
            before=_logical_snapshot(connection)
            connection.execute("PRAGMA query_only=ON")

            def initialized_read():
                with closing(storage.connect_initialized(self.target)):
                    pass

            for boundary,read in (
                ("ordinary schema admission",lambda:schema.validate_storage(connection)),
                ("recovery schema admission",lambda:schema.validate_storage(connection,recovery=True)),
                ("global core admission",lambda:storage.validate_current_database_structure(connection,self.basis["project_id"])),
                ("initialized core admission",initialized_read),
                ("schema reentry",lambda:schema.migrate(connection)),
            ):
                with self.subTest(boundary=boundary):
                    with self.assertRaises(storage.StorageError) as rejected:
                        read()
                    self.assertEqual(rejected.exception.code,"project_state_unreadable")
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(_logical_snapshot(connection),before)

    def initial_source_artifact(self):
        from task_governance_tool import review_continuation as cv, review_continuation_repository as cr
        from task_governance_tool.review_answer_repository import read_native_collected_captures
        from task_governance_tool.review_answer_transport import CollectedItem, frame_collected_submission, decode_collected_submission
        from task_governance_tool.review_collected_results import add_collected_review_results
        metadata,answer=self.metadata(2)
        extra_metadata,extra_answer=self.metadata(3)
        initial,initial_answer=self.metadata(1)
        prep=metadata["basis"]["preparation_id"]
        selected=(cv.SelectedReviewer(initial["binding"]["reviewer_session_id"],"supplement",None,"Explicit required review"),)
        prior=None
        for sequence in (1,2,3):
            cid="tg_review_continuation_"+f"{sequence:016x}"
            capture=dict(kind="collected",consumer_continuation_id=cid,consumer_slot=2,
                source_preparation_id=prep,source_continuation_id=None,source_slot=2,
                session_id=metadata["source_identity"]["reviewer_session_id"],source_basis_digest=values.basis_digest(metadata["basis"]),
                original_digest=metadata["original"]["digest"],metadata_digest=values.metadata_digest(metadata))
            extra_capture={**capture,"consumer_slot":3,"source_slot":3,
                "session_id":extra_metadata["source_identity"]["reviewer_session_id"],
                "source_basis_digest":values.basis_digest(extra_metadata["basis"]),
                "original_digest":extra_metadata["original"]["digest"],"metadata_digest":values.metadata_digest(extra_metadata)}
            if sequence==1:
                for bad_slots in ((1,3),(3,2),(2,4)):
                    with closing(storage.connect_initialized(self.target)) as connection,connection:
                        connection.execute("BEGIN IMMEDIATE")
                        with self.subTest(native_admission=bad_slots),self.assertRaises(TaskValidationError):
                            cr.admit_locked(connection,basis=self.basis,preparation_id=prep,route="manual",selected=selected,
                                caller=self.caller,retained_collected=(({**capture,"consumer_slot":bad_slots[0]},metadata),
                                ({**extra_capture,"consumer_slot":bad_slots[1]},extra_metadata)),continuation_id=cid)
                        connection.rollback()
            with closing(storage.connect_initialized(self.target)) as connection,connection:
                connection.execute("BEGIN IMMEDIATE")
                retirement=cv.RetirementProof(prior.continuation_id,cv.basis_digest(self.basis),None,None) if prior else None
                record=cr.admit_locked(connection,basis=self.basis,preparation_id=prep,route="manual",selected=selected,
                    caller=self.caller,retirement=retirement,retained_collected=((capture,metadata),(extra_capture,extra_metadata)),continuation_id=cid)
                prior=cr.advance_prepared_locked(connection,record,self.caller)
        context=dict(continuation_id=prior.continuation_id,sequence=prior.sequence,selection_digest=cv.selection_digest(selected))
        initial["basis"]["continuation"]=context
        initial["binding"]["basis_digest"]=values.basis_digest(initial["basis"])
        with closing(storage.connect_initialized(self.target)) as connection:
            captures=tuple(c for c,m in read_native_collected_captures(connection,prior.continuation_id))
        raw=frame_collected_submission(prep,(CollectedItem(1,initial_answer.raw,initial,initial_answer),CollectedItem(2,answer.raw,metadata,answer),CollectedItem(3,extra_answer.raw,extra_metadata,extra_answer)),continuation=context,retained_originals=captures)
        with closing(storage.connect_initialized(self.target)) as connection,connection:
            registered=add_collected_review_results(connection,self.target.project,self.basis["task_id"],decode_collected_submission(raw),caller=self.caller,database_target=self.target)
            self.assertIsNone(registered["review_gate"]["blocking_code"])
        verified=add_receipt(self.db,self.fixture.repo,self.basis["task_id"],self.basis["target_generation"])
        self.assertEqual(verified.returncode,0,verified.stdout)
        completed=completion(self.db,self.fixture.repo,self.basis["task_id"])
        self.assertEqual(completed.returncode,0,completed.stdout)
        with closing(storage.connect_initialized(self.target)) as connection:
            schema.validate_storage(connection)
            _,artifacts=_bundle_artifacts(connection,self.basis["project_id"])
            return next(iter(artifacts.values()))

    def test_collected_original_survives_two_pending_supersessions_and_seals_public_graph(self):
        from task_governance_tool import review_continuation_repository as cr
        artifact=self.initial_source_artifact()
        with closing(storage.connect_initialized(self.target)) as connection:
            graph=artifact.payload["review_continuation_basis"]
            self.assertEqual(graph["version"],2)
            self.assertEqual(len(graph["collected_captures"]),6)
            self.assertEqual(sum(c["result_receipt_id"] is not None for c in graph["collected_captures"]),2)
            self.assertNotIn(b'original.json',artifact.document)
            from tests import evidence_reader_oracle as oracle
            from tests.review_continuation_evidence_oracle import validate_graph
            self.assertEqual(validate_graph(artifact.payload,oracle),graph)
            from task_governance_tool import review_continuation_evidence as producer_graph
            from task_governance_tool.evidence_ledger import EvidenceLedgerError
            changed=copy.deepcopy(artifact.payload)
            changed_capture=changed["review_continuation_basis"]["collected_captures"][0]
            changed_capture["source_metadata"]["basis"]["request_digest"]="sha256:"+"f"*64
            changed_capture["source_metadata"]["binding"]["basis_digest"]=values.basis_digest(changed_capture["source_metadata"]["basis"])
            changed_capture["capture_identity"]["source_basis_digest"]=changed_capture["source_metadata"]["binding"]["basis_digest"]
            changed_capture["evidence_digest"]=values.capture_evidence_digest(changed_capture["capture_identity"],changed_capture["source_metadata"])
            with self.assertRaises(EvidenceLedgerError):producer_graph.validate_graph(changed["review_continuation_basis"],bundle=changed)
            with self.assertRaises(oracle.EvidenceConsumerError):validate_graph(changed,oracle)
            for slots in ((1,3),(3,2),(2,4)):
                changed=copy.deepcopy(artifact.payload)
                for row,slot in zip(changed["review_continuation_basis"]["collected_captures"][:2],slots):
                    row["capture_identity"]["consumer_slot"]=slot
                    row["evidence_digest"]=values.capture_evidence_digest(row["capture_identity"],row["source_metadata"])
                with self.subTest(slots=slots):
                    with self.assertRaises(EvidenceLedgerError):producer_graph.validate_graph(changed["review_continuation_basis"],bundle=changed)
                    with self.assertRaises(oracle.EvidenceConsumerError):validate_graph(changed,oracle)
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("DROP TRIGGER trg_review_collected_captures_publish")
                    for row in changed["review_continuation_basis"]["collected_captures"][:2]:
                        stored={**row,"result_receipt_id":None}
                        connection.execute("UPDATE review_collected_captures SET projection_json=? WHERE continuation_id=? AND session_id=?",
                            (json.dumps(stored,sort_keys=True,separators=(',',':')),row["capture_identity"]["consumer_continuation_id"],row["capture_identity"]["session_id"]))
                    with self.assertRaises(storage.StorageError):cr.read_continuations(connection,project_id=self.basis["project_id"],task_id=self.basis["task_id"])
                    connection.rollback()

    def test_resealed_frozen_capture_omissions_fail_readonly_reentry_and_projection(self):
        from task_governance_tool.evidence_ledger import canonical_json_bytes
        from task_governance_tool.evidence_projection import build_bundle_artifact
        from task_governance_tool.review_continuation_evidence_repository import validate_frozen_against_core
        artifact=self.initial_source_artifact()
        graph=artifact.payload["review_continuation_basis"]
        self.assertEqual(len(graph["collected_captures"]),6)
        mutations={
            "all captures omitted": lambda rows: [],
            "historical consumer omitted": lambda rows: rows[2:],
            "registered consumer omitted": lambda rows: rows[:-2],
            "last slot omitted in every consumer": lambda rows: rows[::2],
        }
        with closing(storage.connect_initialized(self.target)) as original:
            for name,omit in mutations.items():
                with self.subTest(omission=name),closing(sqlite3.connect(":memory:")) as connection:
                    connection.row_factory=sqlite3.Row
                    connection.execute("PRAGMA foreign_keys=ON")
                    original.backup(connection)
                    changed=copy.deepcopy(artifact.payload)
                    changed_graph=changed["review_continuation_basis"]
                    changed_graph["collected_captures"]=omit(changed_graph["collected_captures"])
                    # Initial-source registration history is not inferable from
                    # this public graph. Recompute the actual Bundle seal and
                    # length so only native inventory comparison exposes loss.
                    resealed=build_bundle_artifact(changed)
                    trigger=connection.execute("SELECT sql FROM sqlite_master WHERE name='trg_completion_evidence_bundles_no_update'").fetchone()[0]
                    connection.execute("DROP TRIGGER trg_completion_evidence_bundles_no_update")
                    connection.execute("UPDATE completion_evidence_bundles SET continuation_basis_json=?,bundle_digest=?,payload_size_bytes=?",
                        (canonical_json_bytes(changed_graph).decode(),resealed.bundle_digest,len(resealed.payload_bytes)))
                    connection.execute(trigger)
                    connection.commit()
                    schema.validate_owned_contract(connection)
                    before=_logical_snapshot(connection)
                    connection.execute("PRAGMA query_only=ON")
                    for boundary,code,read in (("native comparison","evidence_ledger_inconsistent",lambda:validate_frozen_against_core(connection,changed_graph)),
                                               ("schema reentry","project_state_unreadable",lambda:schema.migrate(connection)),
                                               ("projection","evidence_ledger_inconsistent",lambda:_bundle_artifacts(connection,self.basis["project_id"]))):
                        with self.subTest(boundary=boundary):
                            with self.assertRaises(storage.StorageError) as rejected:
                                read()
                            self.assertEqual(rejected.exception.code,code)
                            if boundary == "schema reentry":
                                # Global admission preserves its existing
                                # sanitized wrapper around the native failure.
                                self.assertIsInstance(rejected.exception.__cause__,storage.StorageError)
                                self.assertEqual(rejected.exception.__cause__.code,"evidence_ledger_inconsistent")
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_collected_captures").fetchone()[0],6)
                    self.assertFalse(connection.in_transaction)
                    self.assertEqual(_logical_snapshot(connection),before)

    def test_frozen_capture_inventory_ignores_legitimate_later_target_activity(self):
        from tests.verification_receipt_test_support import run_taskgov
        from task_governance_tool import review_continuation as cv, review_continuation_repository as cr
        from task_governance_tool.task_ownership import read_basis
        from task_governance_tool.session_identity import CallerIdentity
        from task_governance_tool.review_continuation_evidence_repository import validate_frozen_against_core
        artifact=self.initial_source_artifact()
        reopened=run_taskgov("task","edit",self.basis["task_id"],"--repo",str(self.fixture.repo),"--db",str(self.db),
            "--status","in_progress","--reopen-reason","Inspect a later target","--json")
        self.assertEqual(reopened.returncode,0,reopened.stdout)
        set_target(self.db,self.fixture.repo,self.basis["task_id"],fingerprint="sha256:"+"e"*64)
        with closing(storage.connect_initialized(self.target)) as connection:
            task=connection.execute("SELECT * FROM tasks WHERE task_id=?",(self.basis["task_id"],)).fetchone()
            owner=read_basis(connection,project_id=self.basis["project_id"],task_id=self.basis["task_id"])
            self.caller=CallerIdentity(owner.owner_session_id)
            self.basis={**self.basis,**{key:task["review_target_"+key.removeprefix("target_")]
                for key in ("target_kind","target_value","target_base_revision","target_generation")},
                "authority_snapshot_id":task["review_target_authority_snapshot_id"],
                "acceptance_criterion_id":task["review_target_acceptance_criterion_id"],
                "verification_criterion_id":task["review_target_verification_criterion_id"],
                "artifact_manifest_id":task["review_target_artifact_manifest_id"],
                "completion_commit_required":task["completion_commit_required"],
                "parent_session_id":owner.owner_session_id,"execution_id":owner.execution_id,"ownership_generation":owner.generation}
        metadata,_=self.metadata(5)
        metadata["basis"]["preparation_id"]="tg_review_preparation_ffffffffffffffff"
        metadata["binding"].update(slot=2,basis_digest=values.basis_digest(metadata["basis"]))
        cid="tg_review_continuation_ffffffffffffffff"
        capture=dict(kind="collected",consumer_continuation_id=cid,consumer_slot=2,
            source_preparation_id=metadata["basis"]["preparation_id"],source_continuation_id=None,source_slot=2,
            session_id=metadata["source_identity"]["reviewer_session_id"],source_basis_digest=values.basis_digest(metadata["basis"]),
            original_digest=metadata["original"]["digest"],metadata_digest=values.metadata_digest(metadata))
        selected=(cv.SelectedReviewer("10000000-0000-4000-8000-000000000004","supplement",None,"Review the later target"),)
        with closing(storage.connect_initialized(self.target)) as connection,connection:
            connection.execute("BEGIN IMMEDIATE")
            record=cr.admit_locked(connection,basis=self.basis,preparation_id=metadata["basis"]["preparation_id"],
                route="manual",selected=selected,caller=self.caller,retained_collected=((capture,metadata),),continuation_id=cid)
            cr.advance_prepared_locked(connection,record,self.caller)
        with closing(storage.connect_initialized(self.target)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM review_collected_captures").fetchone()[0],7)
            before=_logical_snapshot(connection)
            connection.execute("PRAGMA query_only=ON")
            self.assertFalse(schema.migrate(connection))
            validate_frozen_against_core(connection,artifact.payload["review_continuation_basis"])
            _,artifacts=_bundle_artifacts(connection,self.basis["project_id"])
            self.assertEqual(next(iter(artifacts.values())),artifact)
            self.assertEqual(_logical_snapshot(connection),before)


class AnswerMigrationTests(unittest.TestCase):
    def test_predecessor_sealed_bytes_and_unrelated_rows_survive_migration_and_failure(self):
        fixture=continuation_fixture.ContinuationRegistrationTests()
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.native_continuation_artifact()
        with closing(storage.connect(fixture.db)) as connection:
            _add_unrelated_objects(connection)
            old_snapshot=_logical_snapshot(connection)
            _,old_bundles=_bundle_artifacts(connection,fixture.basis["project_id"])
            apply=schema._apply_delta_locked
            def interrupted(c):
                apply(c)
                raise storage.StorageError("test_interruption","fixture interruption")
            with candidate_runtime(30):
                with mock.patch.object(schema,"_apply_delta_locked",side_effect=interrupted):
                    with self.assertRaises(storage.StorageError):schema.migrate(connection)
                self.assertEqual(_logical_snapshot(connection),old_snapshot)
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0],1)
                self.assertEqual(connection.execute("PRAGMA legacy_alter_table").fetchone()[0],0)
                self.assertTrue(schema.migrate(connection))
                _,new_bundles=_bundle_artifacts(connection,fixture.basis["project_id"])
                self.assertEqual(old_bundles,new_bundles)
                final=_logical_snapshot(connection)
                connection.execute("PRAGMA query_only=ON")
                self.assertFalse(schema.migrate(connection))
                self.assertFalse(connection.in_transaction)
                self.assertEqual(_logical_snapshot(connection),final)


if __name__=="__main__":unittest.main()
