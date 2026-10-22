"""Synthetic privacy mismatch and explicit installed recovery; no live originals."""
import json
import subprocess
import sys
import unittest
from contextlib import closing
from unittest.mock import patch

from tests.test_review_answer_pure_decoder import payload
from tests import test_review_handoff_preparation as preparation
from tests import test_review_answer_tracking_installed as tracking
from tests import test_review_answer_installed_finalization as finalization
from task_governance_tool import review_answer as answer


def legacy_summaries(value):
    return value["summary"].strip(), [f["summary"].strip() for f in value["findings"]]


LEGACY_POLICY = '''
from unittest.mock import patch
from task_governance_tool import review_answer as answer
def legacy_summaries(value):
 return value['summary'].strip(),[f['summary'].strip() for f in value['findings']]
patch.object(answer,'storage_summaries',legacy_summaries).start()
'''

OLD_REGISTRATION = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from unittest.mock import patch
from task_governance_tool import review_answer as answer,review_collected_results as results
from task_governance_tool.review_finalization import Finalizer
from task_governance_tool.session_identity import capture_caller_identity
from task_governance_tool.task_values import validation_error
def old_fields(value):return value['summary'].strip(),[f['summary'].strip() for f in value['findings']]
def reject(*args):raise validation_error('privacy_rejected','Synthetic storage-field rejection','review_receipt_summary')
service=Finalizer(Path(sys.argv[2]),sys.argv[3],capture_caller_identity())
with patch.object(answer,'storage_summaries',old_fields),patch.object(results,'_normalize',reject):
 from task_governance_tool.review_handoff_originals import submission
 old=service.journal.read()
 _,cached=submission(service.repo,old.packet_path,old.result_paths)
 (service.repo/'reviews'/'synthetic-stale-batch.json').write_bytes(cached)
 report=service.execute()
record=service.journal.read()
assert record.registration=='dispatching' and record.commit==record.completion=='not_started'
assert not record.receipt_ids
print(json.dumps(dict(registration=record.registration,commit=record.commit,completion=record.completion)))
'''

LATE_WRITER = r'''
import io,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from unittest.mock import patch
from task_governance_tool import cli,review_answer as answer
def old_fields(value):return value['summary'].strip(),[f['summary'].strip() for f in value['findings']]
class Input:buffer=io.BytesIO((Path(sys.argv[2])/'reviews'/'synthetic-stale-batch.json').read_bytes())
with patch.object(sys,'stdin',Input()),patch.object(answer,'storage_summaries',old_fields):
 raise SystemExit(cli.main(['--repo',sys.argv[2],'review','result','add',sys.argv[3],'--json']))
'''

PARTIAL_PROJECTION = r'''
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from task_governance_tool import review_handoff_continuation as helper
from task_governance_tool.review_handoff import main
actual=helper.core.read_state
def partial(*args,**kwargs):
 return replace(actual(*args,**kwargs),heads=(SimpleNamespace(session_id=sys.argv[2]),))
with patch.object(helper.core,'read_state',partial):
 raise SystemExit(main(sys.argv[3:]))
'''

INTERRUPTED_CONTINUATION = r'''
import json,sys,subprocess
sys.path.insert(0,sys.argv[1])
from unittest.mock import patch
from task_governance_tool import review_handoff_continuation as helper
from task_governance_tool.review_handoff import main
from task_governance_tool import review_finalization as finalizer
boundary=sys.argv[2]
if boundary=='fence':owner,method=helper.TransitionRepository,'fence'
elif boundary=='admit':owner,method=helper.core,'admit'
elif boundary=='finalizer':owner,method=finalizer,'prepare_continuation'
elif boundary=='prepared':owner,method=helper.core,'prepared'
else:raise AssertionError('unknown boundary')
actual=getattr(owner,method)
def lost(*args,**kwargs):
 if boundary=='admit':
  command=sys.argv[3:];repo=command[command.index('--repo')+1];task=command[command.index('--task-id')+1]
  late=subprocess.run([sys.executable,'-I','-S','-B','-c',LATE_WRITER,sys.argv[1],repo,task],capture_output=True)
  assert late.returncode!=0,'competing old writer crossed held retirement admission'
 result=actual(*args,**kwargs)
 raise OSError('Synthetic acknowledgement interruption')
with patch.object(owner,method,lost):code=main(sys.argv[3:])
raise SystemExit(code)
'''
INTERRUPTED_CONTINUATION = "LATE_WRITER=" + repr(LATE_WRITER) + "\n" + INTERRUPTED_CONTINUATION


class RejectedAnswerValuesTests(unittest.TestCase):
    def test_storage_field_gap_rejected_before_digest_retention(self):
        for field in ("summary", "findings[].summary"):
            value = payload()
            if field == "summary":
                value["summary"] = "stdout: synthetic fixture output"
            else:
                value["findings"] = [dict(severity="medium", summary="stderr: synthetic fixture output")]
            raw = json.dumps(value).encode()
            with patch.object(answer, "storage_summaries", legacy_summaries):
                old = answer.decode_answer(raw)
            with self.subTest(field=field), self.assertRaises(answer.AnswerError) as caught:
                answer.decode_answer(raw)
            self.assertEqual(caught.exception.failure, dict(
                **{"class": "privacy_rejected"}, field=field, reason="privacy"))
            self.assertIsNone(caught.exception.observed_digest)
            self.assertFalse(caught.exception.privacy_admissible)
            original = dict(byte_length=len(raw), payload_start=old.payload_start,
                payload_length=old.payload_length, digest=old.original_digest, payload_digest=old.payload_digest)
            facts = answer.inspect_retirement_rejection(raw, original)
            self.assertEqual(facts["field"], field)
            self.assertNotIn("synthetic", json.dumps(facts))
            self.assertNotIn("digest", json.dumps(facts))
            with self.assertRaises(answer.AnswerError):
                answer.inspect_retirement_rejection(raw, {**original, "digest": "sha256:" + "0"*64})

    def test_legitimate_negative_unable_and_ordinary_pass_are_not_retireable(self):
        for verdict in ("pass", "changes_requested", "unable_to_review"):
            value = {**payload(), "verdict": verdict,
                "findings": [dict(severity="medium", summary="Check the unchanged boundary")],
                "checks": ["Inspected stdout handling"], "limitations": ["No external validation"]}
            raw = json.dumps(value).encode()
            decoded = answer.decode_answer(raw)
            self.assertEqual(decoded.payload, value)
            original = dict(byte_length=len(raw), payload_start=decoded.payload_start,
                payload_length=decoded.payload_length, digest=decoded.original_digest, payload_digest=decoded.payload_digest)
            with self.assertRaises(answer.AnswerError):
                answer.inspect_retirement_rejection(raw, original)

    def test_generic_secret_shape_and_capacity_keep_distinct_failures(self):
        cases = [(b"not json", "format_invalid"), (b" "*262145, "capacity_exceeded"),
            (json.dumps({**payload(), "summary": "Authorization: Bearer synthetic-token"}).encode(), "privacy_rejected")]
        for raw, expected in cases:
            with self.subTest(expected=expected), self.assertRaises(answer.AnswerError) as caught:
                answer.decode_answer(raw)
            self.assertEqual(caught.exception.failure["class"], expected)

    def test_replacement_audit_is_closed_and_does_not_accept_registered_sources(self):
        from task_governance_tool.review_answer_rejection_values import validate
        from tests.test_review_answer_tracking_collection import tracking_fixture
        meta, _ = tracking_fixture()
        source = dict(source_continuation_id=None, slot=1, **meta["source_identity"],
            registration=dict(state="dispatched", submission_digest="sha256:" + "c"*64, receipt_id=None),
            rejection=dict(field="summary", verdict="changes_requested", severities=["medium"]))
        audit = dict(version=1, source_continuation_id=None, sources=[source])
        self.assertEqual(validate(audit), audit)
        for changed in ({**audit, "body": "forbidden"}, {**audit, "sources": [source, source]},
            {**audit, "sources": [{**source, "rejection": None}]},
            {**audit, "sources": [{**source, "registration": dict(state="registered",
                submission_digest="sha256:" + "c"*64, receipt_id="tg_review_receipt_" + "1"*16)}]}):
            with self.assertRaises(answer.AnswerError):
                validate(changed)

    def test_binding_uses_storage_reviewer_key_privacy_before_freezing(self):
        from tests.test_review_answer_tracking_collection import tracking_fixture
        from task_governance_tool.review_answer_values import validate_binding
        meta, _ = tracking_fixture()
        binding = {**meta["binding"], "reviewer_key": "stdout: synthetic reviewer label"}
        with self.assertRaises(answer.AnswerError) as caught:
            validate_binding(binding, meta["basis"])
        self.assertEqual(caught.exception.failure["class"], "privacy_rejected")
        self.assertIsNone(caught.exception.observed_digest)

    def test_privacy_safe_semantic_failure_keeps_existing_digest_diagnostic(self):
        raw = json.dumps({**payload(), "summary": " "}).encode()
        with self.assertRaises(answer.AnswerError) as caught:
            answer.decode_answer(raw)
        self.assertTrue(caught.exception.privacy_admissible)
        self.assertEqual(caught.exception.observed_digest, answer.byte_digest(raw))

    def test_new_registration_attempt_cannot_reset_or_erase_old_attempt(self):
        from tests.test_review_answer_tracking_collection import tracking_fixture
        from task_governance_tool.review_answer_collection_repository import _forward
        from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
        import copy
        from task_governance_tool.review_answer_collection_repository import initial_record
        meta, _ = tracking_fixture()
        old = initial_record(meta["basis"])
        old["registration"] = dict(state="dispatched", submission_digest="sha256:"+"a"*64, receipt_id=None)
        new = copy.deepcopy(old)
        new["retired_registrations"] = [dict(continuation_id="tg_review_continuation_"+"1"*16,
                                             registration=copy.deepcopy(old["registration"]))]
        new["registration"]["submission_digest"] = "sha256:"+"b"*64
        _forward(old, new)
        for changed in (dict(new, retired_registrations=[]),
            dict(new, registration=dict(state="not_started", submission_digest=None, receipt_id=None))):
            with self.assertRaises(RepositoryError):
                _forward(new, changed)


class RejectedAnswerRepositoryTests(unittest.TestCase):
    def test_fresh_writer_accepts_absence_and_refuses_partial_registered_source(self):
        from tests.review_answer_fixture_support import review_fixture
        from task_governance_tool import storage, review_continuation as values
        from task_governance_tool.review_answer_rejection_repository import require_absent_locked
        from task_governance_tool.task_values import TaskValidationError
        with review_fixture(schema_version=31, contract=None) as fixture:
            metadata, decoded = fixture.metadata(1)
            meta2, _ = fixture.metadata(2)
            sources = [dict(source_continuation_id=None, slot=i, **meta["source_identity"],
                registration=dict(state="dispatched", submission_digest="sha256:" + "d"*64, receipt_id=None),
                rejection=dict(field="summary", verdict="pass", severities=[]) if i == 1 else None)
                for i, meta in enumerate((metadata, meta2), 1)]
            sources.sort(key=lambda s: s["reviewer_session_id"])
            audit = dict(version=1, source_continuation_id=None, sources=sources)
            selected = (values.SelectedReviewer(metadata["source_identity"]["reviewer_session_id"],
                "supplement", None, "Correct rejected summary"),)
            with closing(storage.connect_initialized(fixture.target)) as connection, connection:
                storage.begin_initialized_write(connection, fixture.target)
                require_absent_locked(connection, fixture.basis, metadata["basis"]["preparation_id"], selected, audit)
            with closing(storage.connect_initialized(fixture.target)) as connection, connection:
                fixture.add(connection, 2)  # One legitimate sibling committed: no whole-batch absence.
            with closing(storage.connect_initialized(fixture.target)) as connection, connection:
                storage.begin_initialized_write(connection, fixture.target)
                with self.assertRaises(TaskValidationError) as caught:
                    require_absent_locked(connection, fixture.basis, metadata["basis"]["preparation_id"], selected, audit)
                self.assertEqual(caught.exception.code, "review_continuation_effect_unknown")


class RejectedAnswerInstalledTests(preparation.PreparationFixture):
    git = preparation.ReviewerMaterialTests.git
    committed_fixture = preparation.ReviewerMaterialTests.committed_fixture
    prepared = finalization.InstalledAnswerFinalizationTests.prepared
    finalize = finalization.InstalledAnswerFinalizationTests.finalize
    collect_public = tracking.InstalledAnswerTrackingTests.collect_public
    assert_collected = tracking.InstalledAnswerTrackingTests.assert_collected
    current_receipts = tracking.InstalledAnswerTrackingTests.current_receipts

    def setUp(self):
        super().setUp()
        self.cli("setup", "--review-answer-collection", "on", "--review-wait", "off")
        self.task_id, self.handoff = self.prepared(contract=True)

    def old_batch(self, *, sibling="pass", dispatched=True):
        bad = json.dumps({**payload(), "summary": "stdout: synthetic fixture output"})
        arrivals = [tracking.arrival(1, text=bad), tracking.arrival(2, sibling)]
        script = tracking.PUBLIC_COLLECTION.replace("sys.path.insert(0,sys.argv[1])",
            "sys.path.insert(0,sys.argv[1])\n" + LEGACY_POLICY)
        completed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script,
            str(self.install.skill_root / "scripts"), str(self.root)],
            input=json.dumps(dict(task_id=self.task_id, arrivals=arrivals)).encode(),
            cwd=self.root, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_collected(json.loads(completed.stdout), [1, 2])
        originals = {r["result_path"]: (self.root/r["result_path"]).read_bytes()
                     for r in self.handoff["review_requests"]}
        if dispatched:
            completed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", OLD_REGISTRATION,
                str(self.install.skill_root / "scripts"), str(self.root), self.task_id],
                cwd=self.root, capture_output=True)
            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(self.current_receipts(), [])
        return originals, arrivals

    def replace_rejected(self, *, directory="reviews/replacement", enabled=True):
        selection = dict(version=1, reviewers=[dict(session_id=tracking.reviewer(1), action="supplement",
            prior_receipt_id=None, reason="Provide a privacy-safe judgment on the same material")])
        args = ["continue", "--repo", str(self.root), "--task-id", self.task_id,
            "--target-generation", "1", "--directory", directory]
        if enabled:
            args.append("--replace-rejected-answers")
        result = self.invoke(*args, raw=json.dumps(selection).encode())
        return result, json.loads(result.stdout)

    def test_dispatched_rejection_preserves_sibling_and_finishes_once(self):
        originals, arrivals = self.old_batch()
        failed, _ = self.replace_rejected(enabled=False)
        self.assertNotEqual(failed.returncode, 0)
        selection = dict(version=1, reviewers=[dict(session_id=tracking.reviewer(1), action="supplement",
            prior_receipt_id=None, reason="Provide a privacy-safe judgment on the same material")])
        partial = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", PARTIAL_PROJECTION,
            str(self.install.skill_root / "scripts"), tracking.reviewer(2),
            "continue", "--repo", str(self.root), "--task-id", self.task_id,
            "--target-generation", "1", "--directory", "reviews/partial", "--replace-rejected-answers"],
            input=json.dumps(selection).encode(), cwd=self.root, capture_output=True)
        self.assertNotEqual(partial.returncode, 0)
        self.assertEqual(json.loads(partial.stdout)["errors"][0]["code"], "review_continuation_effect_unknown")
        self.assertFalse((self.root/"reviews/partial").exists())
        result, response = self.replace_rejected()
        self.assertEqual(result.returncode, 0, response)
        self.handoff = response["handoff"]
        late = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", LATE_WRITER,
            str(self.install.skill_root / "scripts"), str(self.root), self.task_id],
            cwd=self.root, capture_output=True)
        self.assertNotEqual(late.returncode, 0)
        self.assertEqual(self.current_receipts(), [])
        again, repeated = self.replace_rejected()
        self.assertEqual(again.returncode, 0, repeated)
        self.assertEqual(repeated["handoff"]["review_requests"], [])
        self.collect_public([arrivals[0]], action="arm")
        self.assert_collected(self.collect_public([tracking.arrival(1, revision=2)]), [1])
        result, report = self.finalize(self.task_id)
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(report["status"], "completed")
        head = self.git("rev-parse", "HEAD")
        result, report = self.finalize(self.task_id)
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        for path, raw in originals.items():
            self.assertEqual((self.root/path).read_bytes(), raw)

    def test_not_dispatched_replacement_keeps_negative_sibling_blocking(self):
        originals, arrivals = self.old_batch(sibling="changes_requested", dispatched=False)
        result, response = self.replace_rejected()
        self.assertEqual(result.returncode, 0, response)
        self.collect_public([arrivals[0]], action="arm")
        self.assert_collected(self.collect_public([tracking.arrival(1, revision=2)]), [1])
        before = self.git("rev-parse", "HEAD")
        result, report = self.finalize(self.task_id)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(report["stages"]["registration"], "succeeded", report)
        self.assertEqual(report["blocking_code"], "review_required", report)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        for path, raw in originals.items():
            self.assertEqual((self.root/path).read_bytes(), raw)

    def test_later_rejected_round_keeps_root_sibling_and_native_capture_history(self):
        originals, arrivals = self.old_batch()
        result, response = self.replace_rejected()
        self.assertEqual(result.returncode, 0, response)
        first_id = response["handoff"]["continuation_id"]
        self.collect_public([arrivals[0]], action="arm")
        script = tracking.PUBLIC_COLLECTION.replace("sys.path.insert(0,sys.argv[1])",
            "sys.path.insert(0,sys.argv[1])\n" + LEGACY_POLICY)
        bad = json.dumps({**payload(), "summary": "stdout: synthetic corrected fixture output"})
        completed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script,
            str(self.install.skill_root / "scripts"), str(self.root)],
            input=json.dumps(dict(task_id=self.task_id,
                arrivals=[tracking.arrival(1, revision=2, text=bad)])).encode(),
            cwd=self.root, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assert_collected(json.loads(completed.stdout), [1])
        first_path = response["handoff"]["review_requests"][0]["result_path"]
        originals[first_path] = (self.root/first_path).read_bytes()
        result, response = self.replace_rejected(directory="reviews/replacement-second")
        self.assertEqual(result.returncode, 0, response)
        self.assertEqual(response["handoff"]["continuation_sequence"], 2)
        self.assertNotEqual(response["handoff"]["continuation_id"], first_id)
        self.collect_public([tracking.arrival(1, revision=2)], action="arm")
        self.assert_collected(self.collect_public([tracking.arrival(1, revision=3)]), [1])
        result, report = self.finalize(self.task_id)
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(report["status"], "completed")
        for path, raw in originals.items():
            self.assertEqual((self.root/path).read_bytes(), raw)

    def test_interrupted_fence_native_admission_and_publication_reconcile_same_operation(self):
        originals, _ = self.old_batch()
        selection = dict(version=1, reviewers=[dict(session_id=tracking.reviewer(1), action="supplement",
            prior_receipt_id=None, reason="Provide a privacy-safe judgment on the same material")])
        arguments = ["continue", "--repo", str(self.root), "--task-id", self.task_id,
            "--target-generation", "1", "--directory", "reviews/replacement", "--replace-rejected-answers"]
        for boundary in ("fence", "admit", "finalizer", "prepared"):
            with self.subTest(boundary=boundary):
                result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", INTERRUPTED_CONTINUATION,
                    str(self.install.skill_root / "scripts"), boundary, *arguments],
                    input=json.dumps(selection).encode(), cwd=self.root, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                response = json.loads(result.stdout)
                # A lost fence return cannot fabricate its unacknowledged ID;
                # the same directory must discover it on the next invocation.
                self.assertEqual(response["handoff"]["status"],
                    "unavailable" if boundary == "fence" else "pending", response)
                competing, _ = self.replace_rejected(directory="reviews/competing")
                self.assertNotEqual(competing.returncode, 0)
        result, response = self.replace_rejected()
        self.assertEqual(result.returncode, 0, response)
        self.assertEqual(response["handoff"]["review_requests"], [])
        self.assertEqual(response["handoff"]["continuation_sequence"], 1)
        self.assertEqual(self.current_receipts(), [])
        for path, raw in originals.items():
            self.assertEqual((self.root/path).read_bytes(), raw)
