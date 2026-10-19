"""First-valid answer tracking through installed public collection and completion.

Only public host pages are mocked. No collection binding, answer, operational
record or Receipt is injected; every subprocess starts with fresh runtime state.
"""
from __future__ import annotations

import json
import subprocess
import sys

from tests import test_review_answer_installed_finalization as finalization_fixture
from tests import test_review_handoff_preparation as fixtures


def reviewer(slot):
    return "91000000-0000-4000-8000-" + format(1000 + slot, "012x")


def turn(slot, revision=1):
    return "91000000-0000-4000-8000-" + format(2000 + 100 * revision + slot, "012x")


def answer(slot, verdict="pass", revision=1):
    return " \r\n```json\r\n" + json.dumps({
        "verdict": verdict,
        "summary": f"Assigned review {slot}, judgment {revision}: 日本語の原本",
        "findings": [],
        "checks": ["Complete assigned material inspected"],
        "limitations": ["Offline public host fixture"],
    }, ensure_ascii=False, separators=(",", ":")) + "\r\n```\r\n"


def arrival(slot, verdict="pass", revision=1, *, text=None, status="completed"):
    return {"session": reviewer(slot), "turn": turn(slot, revision),
            "message": f"message-{slot}-{revision}", "status": status,
            "answer": answer(slot, verdict, revision) if text is None else text}


PUBLIC_COLLECTION = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from task_governance_tool.review_wait_runtime.project_server import ProjectConfig,ProjectReviewWaitSession
from task_governance_tool.review_wait_runtime.review_wait_server import _call
from task_governance_tool.session_identity import capture_caller_identity
repo,request=Path(sys.argv[2]),json.loads(sys.stdin.buffer.read())
parent=capture_caller_identity().require()
parent_turn='91000000-0000-4000-8000-000000000001'
rows=request['arrivals'];reads=[]

def page(thread,limit,tid,items,status='completed'):
 return dict(schemaVersion=1,thread=dict(id=thread,kind='codex',hostId='local'),
  page=dict(order='newest_first',limit=limit,hasMore=False,nextCursor=None),
  turns=[dict(id=tid,status=status,items=items,error=None,startedAt=1,
   completedAt=2 if status=='completed' else None,durationMs=1 if status=='completed' else None)])

class Host:
 def __init__(self,metadata):self._metadata=metadata
 def _json_call(self,method,args):
  assert method=='read_thread', 'collection must not send or schedule'
  assert args['hostId']=='local' and args['includeOutputs'] is False and args['maxOutputCharsPerItem']==256
  assert set(args)=={'threadId','hostId','turnLimit','includeOutputs','maxOutputCharsPerItem'}
  thread,limit=args['threadId'],args['turnLimit'];reads.append([thread,limit])
  assert limit in (1,2)
  if thread==parent:
   return page(parent,limit,parent_turn,[dict(type='subAgentActivity',id='dispatch-'+str(index),
    kind='started',agentThreadId=row['session']) for index,row in enumerate(rows,1)])
  row=next(row for row in rows if row['session']==thread)
  items=[] if row['status']!='completed' else [dict(type='agentMessage',id=row['message'],phase='final_answer',text=row['answer'])]
  return page(thread,limit,row['turn'],items,row['status'])

def no_worker(*args,**kwargs):raise AssertionError('parent collection started a wait worker')
session=ProjectReviewWaitSession(ProjectConfig(repo,Path(sys.argv[1])/'review_wait_host.mjs',repo,'UTC'),
 managed_host_factory=lambda **kwargs:Host(kwargs['metadata']),session_factory=no_worker)
arguments=dict(task_id=request['task_id'],reviewer_ids=[row['session'] for row in rows])
if request.get('action') is not None:arguments['action']=request['action']
response=_call(dict(name='review_answer_collect',arguments=arguments,
 _meta=dict(threadId=parent,turnId=parent_turn)),session)
print(json.dumps(dict(response=json.loads(response['content'][0]['text']),reads=reads),ensure_ascii=True))
'''


class InstalledAnswerTrackingTests(fixtures.PreparationFixture):
    git = fixtures.ReviewerMaterialTests.git
    committed_fixture = fixtures.ReviewerMaterialTests.committed_fixture
    prepared = finalization_fixture.InstalledAnswerFinalizationTests.prepared
    finalize = finalization_fixture.InstalledAnswerFinalizationTests.finalize

    def setUp(self):
        super().setUp()
        self.cli("setup", "--review-answer-collection", "on", "--review-wait", "off")
        self.task_id, self.handoff = self.prepared(contract=True)
        self.initial = self.handoff
        self.initial_paths = tuple(row["result_path"] for row in self.initial["review_requests"])

    def collect_public(self, arrivals, *, action=None):
        request = dict(task_id=self.task_id, arrivals=arrivals, action=action)
        completed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", PUBLIC_COLLECTION,
            str(self.install.skill_root / "scripts"), str(self.root)],
            input=json.dumps(request).encode("utf-8"), cwd=self.root, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        return json.loads(completed.stdout)

    def assert_collected(self, observed, slots):
        response = observed["response"]
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["collected"], slots, response)
        self.assertEqual(response["unavailable"], [], response)

    def originals(self, paths=None):
        return {path: (self.root / path).read_bytes() for path in (paths or self.initial_paths)}

    def current_receipts(self):
        return self.cli("task", "show", self.task_id)["review_evidence"]["current_receipts"]

    def audited_receipts(self):
        return self.cli("task", "show", self.task_id, "--audit")["review_evidence"]["recent_receipts"]

    def continue_reviewer(self, prior, *, directory="reviews/continued"):
        selection = dict(version=1, reviewers=[dict(session_id=reviewer(1), action="reassess",
            prior_receipt_id=prior, reason="Reassess the unchanged material after the missing explanation")])
        completed = self.invoke("continue", "--repo", str(self.root), "--task-id", self.task_id,
            "--target-generation", "1", "--directory", directory,
            raw=json.dumps(selection).encode("utf-8"))
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        self.handoff = json.loads(completed.stdout)["handoff"]
        self.assertEqual(self.handoff["route"], "integrated")
        self.assertNotIn("submit_command", self.handoff)
        self.assertIn("finalization_command", self.handoff)
        self.assertEqual([row["session_id"] for row in self.handoff["review_requests"]], [reviewer(1)])
        return self.handoff

    def registered_blocker(self, verdict):
        self.assert_collected(self.collect_public([arrival(1, verdict), arrival(2)]), [1, 2])
        originals = self.originals()
        before = self.git("rev-parse", "HEAD")
        completed, report = self.finalize(self.task_id)
        self.assertNotEqual(completed.returncode, 0, report)
        self.assertEqual(report["blocking_code"],
            "review_unable" if verdict == "unable_to_review" else "review_required", report)
        blocking_count = ("unable_current_generation" if verdict == "unable_to_review"
                          else "changes_requested_current_generation")
        self.assertEqual(report["reviews"]["counts"][blocking_count], 1)
        self.assertEqual(report["stages"], dict(registration="succeeded", commit="not_started", completion="not_started"))
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        receipts = self.current_receipts()
        prior = next(row["review_receipt_id"] for row in receipts if row["verdict"] != "pass")
        retained = next(row["review_receipt_id"] for row in receipts if row["verdict"] == "pass")
        return prior, retained, originals

    def assert_reassessment_completes(self, verdict):
        prior, retained, originals = self.registered_blocker(verdict)
        self.continue_reviewer(prior)
        path = self.handoff["review_requests"][0]["result_path"]
        # A fresh continuation cannot reuse the initial child-binding admission.
        unarmed = self.collect_public([arrival(1, verdict)])
        self.assertFalse(unarmed["response"]["ok"], unarmed)
        self.assertFalse((self.root / path).exists())
        armed = self.collect_public([arrival(1, verdict)], action="arm")
        self.assertEqual(armed["response"]["status"], "armed", armed)
        self.assertTrue(armed["response"]["ok"], armed)
        self.assertEqual(armed["reads"], [[reviewer(1), 2]])
        duplicate_arm = self.collect_public([arrival(1, verdict)], action="arm")
        self.assertEqual(duplicate_arm["response"], armed["response"])
        # The terminal answer observed before dispatch is explicitly excluded.
        old = self.collect_public([arrival(1, verdict)])
        self.assertFalse(old["response"]["ok"], old)
        self.assertFalse((self.root / path).exists())
        self.assert_collected(self.collect_public([arrival(1, revision=2)]), [1])
        accepted = (self.root / path).read_bytes()
        self.assertEqual(accepted, answer(1, revision=2).encode("utf-8"))
        # Restarted parent processing cannot replace the first accepted answer.
        self.assert_collected(self.collect_public([arrival(1, "changes_requested", revision=3)]), [1])
        self.assertEqual((self.root / path).read_bytes(), accepted)
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assertEqual(report["status"], "completed", report)
        receipts = self.current_receipts()
        self.assertEqual(len(receipts), 2)
        self.assertIn(retained, [row["review_receipt_id"] for row in receipts])
        self.assertNotIn(prior, [row["review_receipt_id"] for row in receipts])
        successor = next(row for row in receipts if row["review_receipt_id"] != retained)
        self.assertEqual(successor["judgment_revision"], 2)
        audited = next(row for row in self.audited_receipts()
            if row["review_receipt_id"] == successor["review_receipt_id"])
        observed = audited["review_provenance"]["host_observation"]
        self.assertEqual((observed["reviewer_session_id"], observed["turn_id"], observed["message_id"]),
            (reviewer(1), turn(1, 2), "message-1-2"))
        self.assertEqual(observed["binding_source"], dict(adapter="codex-app-tools-review-answer",
            version=2, operation="reviewer_tracking"))
        self.assertEqual(self.originals(), originals)
        self.assertEqual(self.cli("task", "show", self.task_id)["task"]["status"], "done")
        self.assertEqual(self.git("show", "HEAD:source.py"), b"value = 2\n")
        commit = self.git("rev-parse", "HEAD")
        repeated, repeated_report = self.finalize(self.task_id)
        self.assertEqual(repeated.returncode, 0, repeated_report)
        self.assertEqual(repeated_report["registered_receipt_ids"], report["registered_receipt_ids"])
        self.assertEqual(self.git("rev-parse", "HEAD"), commit)
        after_done = self.collect_public([arrival(1, "changes_requested", revision=4)])
        self.assertFalse(after_done["response"]["ok"], after_done)
        self.assertEqual((self.root / path).read_bytes(), accepted)
        self.assertEqual(self.current_receipts(), receipts)

    def test_public_unable_reassessment_completes_and_retains_other_pass(self):
        self.assert_reassessment_completes("unable_to_review")

    def test_public_ng_reassessment_completes_and_retains_other_pass(self):
        self.assert_reassessment_completes("changes_requested")

    def test_malformed_initial_answer_can_be_followed_by_first_valid_answer(self):
        invalid = self.collect_public([arrival(1, text="not a structured review"), arrival(2)])
        self.assertFalse(invalid["response"]["ok"], invalid)
        self.assertFalse((self.root / self.initial_paths[0]).exists())
        sibling = (self.root / self.initial_paths[1]).read_bytes()
        valid = self.collect_public([arrival(1, revision=2), arrival(2)])
        self.assert_collected(valid, [1, 2])
        accepted = self.originals()
        self.assertEqual(accepted[self.initial_paths[0]], answer(1, revision=2).encode("utf-8"))
        self.assertEqual(accepted[self.initial_paths[1]], sibling)
        self.assert_collected(self.collect_public([
            arrival(1, "unable_to_review", revision=3), arrival(2, "changes_requested", revision=2)]), [1, 2])
        self.assertEqual(self.originals(), accepted)
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assertEqual(report["status"], "completed", report)
        observations = {row["review_provenance"]["host_observation"]["reviewer_session_id"]:
            row["review_provenance"]["host_observation"] for row in self.audited_receipts()}
        self.assertEqual(observations[reviewer(1)]["turn_id"], turn(1, 2))
        self.assertEqual(observations[reviewer(2)]["turn_id"], turn(2))
        for observed in observations.values():
            self.assertEqual(observed["binding_source"], dict(adapter="codex-app-tools-review-answer",
                version=2, operation="reviewer_tracking"))

    def bind_pending(self):
        observed = self.collect_public([arrival(1, status="inProgress"), arrival(2, status="inProgress")])
        self.assertTrue(observed["response"]["bound"], observed)
        self.assertEqual(observed["response"]["collected"], [])
        self.assertTrue(all(not (self.root / path).exists() for path in self.initial_paths))

    def assert_stale_unpublished(self):
        stale = self.collect_public([arrival(1), arrival(2)])
        self.assertFalse(stale["response"]["ok"], stale)
        self.assertTrue(all(not (self.root / path).exists() for path in self.initial_paths))
        self.assertEqual(self.current_receipts(), [])

    def test_contract_change_rejects_outstanding_answers_before_publication(self):
        self.bind_pending()
        self.cli("task", "edit", self.task_id, "--contract-scope", "Changed explicitly approved scope",
            "--contract-acceptance", "Changed explicitly approved acceptance",
            "--contract-authority-ref", f"user_instruction:{self.task_id}:2",
            "--contract-change-reason", "Isolated invalidation scenario")
        self.assert_stale_unpublished()

    def test_new_target_rejects_outstanding_answers_before_publication(self):
        self.bind_pending()
        changed = self.invoke("prepare-finalization", "--repo", str(self.root), "--directory=reviews/g2",
            "target", self.task_id, "--kind=git_snapshot")
        self.assertEqual(changed.returncode, 0, changed.stdout or changed.stderr)
        self.assertEqual(json.loads(changed.stdout)["source"]["review_target"]["generation"], 2)
        self.assert_stale_unpublished()

    def test_changed_reviewed_material_rejects_outstanding_answers(self):
        self.bind_pending()
        (self.root / "source.py").write_text("value = 3\n", encoding="utf-8")
        self.git("add", "source.py")
        self.assert_stale_unpublished()

    def test_superseding_unjudged_round_requires_a_new_arm_and_excludes_old_answer(self):
        prior, retained, originals = self.registered_blocker("unable_to_review")
        first = self.continue_reviewer(prior)
        first_path = first["review_requests"][0]["result_path"]
        armed = self.collect_public([arrival(1, "unable_to_review")], action="arm")
        self.assertTrue(armed["response"]["ok"], armed)
        second = self.continue_reviewer(prior, directory="reviews/continued-again")
        self.assertNotEqual(second["continuation_id"], first["continuation_id"])
        second_path = second["review_requests"][0]["result_path"]
        unarmed = self.collect_public([arrival(1, revision=2)])
        self.assertFalse(unarmed["response"]["ok"], unarmed)
        armed = self.collect_public([arrival(1, revision=2)], action="arm")
        self.assertTrue(armed["response"]["ok"], armed)
        excluded = self.collect_public([arrival(1, revision=2)])
        self.assertFalse(excluded["response"]["ok"], excluded)
        self.assertFalse((self.root / first_path).exists())
        self.assertFalse((self.root / second_path).exists())
        self.assert_collected(self.collect_public([arrival(1, revision=3)]), [1])
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assertEqual(report["status"], "completed", report)
        self.assertFalse((self.root / first_path).exists())
        self.assertEqual(self.originals(), originals)
        self.assertIn(retained, [row["review_receipt_id"] for row in self.current_receipts()])
