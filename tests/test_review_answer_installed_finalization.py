"""Installed collected originals through the existing commit/completion path."""
from __future__ import annotations
import hashlib
import json
import subprocess
import sys

from tests import test_review_finalization as old_finalization
from tests import test_review_handoff_preparation as fixtures
from tests import test_review_answer_installed as collection_fixture


FINALIZE = r'''
import json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from task_governance_tool import review_finalization as f
from task_governance_tool.session_identity import capture_caller_identity
repo,task,mode=Path(sys.argv[2]),sys.argv[3],sys.argv[4]
service=f.Finalizer(repo,task,capture_caller_identity())
registered,lookups=0,0
register=f.Core.register;lookup=f.Core.collection_registration
def observe_lookup(self,task,submission):
 global lookups
 lookups+=1
 return lookup(self,task,submission)
def interrupted(self,task,submission,**kwargs):
 global registered
 registered+=1
 register(self,task,submission,**kwargs)
 raise OSError('isolated lost core registration acknowledgement')
def forbidden(*args,**kwargs):raise AssertionError('registered source dispatched twice')
with patch.object(f.Core,'register',interrupted if mode=='lose-registration-output' else forbidden),patch.object(f.Core,'collection_registration',observe_lookup):
 result=service.execute()
print(json.dumps(dict(report=result,registrations=registered,lookups=lookups),ensure_ascii=True))
'''


PREWRITER_FAILURE = r'''
import json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from task_governance_tool import review_finalization as f,review_answer_registration as registration
from task_governance_tool.session_identity import capture_caller_identity
from task_governance_tool.storage import StorageError
repo,task,mode=Path(sys.argv[2]),sys.argv[3],sys.argv[4]
service=f.Finalizer(repo,task,capture_caller_identity())
read=service.core.read
reads=0
def busy(*args,**kwargs):raise StorageError('database_busy','task database is busy')
def preflight_read(*args,**kwargs):
 global reads
 reads+=1
 if reads==2:busy()
 return read(*args,**kwargs)
if mode=='preflight':target,method,value=service.core,'read',preflight_read
elif mode=='reserved':target,method,value=service.core,'register',busy
elif mode=='dispatched':target,method,value=registration,'connect_initialized',busy
else:raise AssertionError('unexpected injection')
with patch.object(target,method,side_effect=value):result=service.execute()
print(json.dumps(result,ensure_ascii=True))
'''


REGISTER_EXIT_FAILURE = r'''
import io,json,sys
from contextlib import contextmanager,redirect_stdout
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from task_governance_tool import cli,review_answer_registration as registration
from task_governance_tool.review_answer_preparation import locate
from task_governance_tool.review_answer_handoff import initial_submission,RegistrationAdmission
from task_governance_tool.review_handoff_transport import decode_packet
from task_governance_tool.review_wait_runtime.review_wait_repository import RepositoryError
repo,packet_path=Path(sys.argv[2]),sys.argv[3]
packet_raw=(repo/packet_path).read_bytes()
resolution,root=locate(repo,packet_path,packet_raw,decode_packet(packet_raw))
task,raw=initial_submission(repo,root,resolution,root.result_paths)
admission=registration.registration_admission
actual_confirm=RegistrationAdmission.confirm
confirmations=0
@contextmanager
def failed_exit(*args,**kwargs):
 with admission(*args,**kwargs) as admitted:
  yield admitted
 raise RepositoryError('SENTINEL private lease cleanup detail')
def confirm(self,result):
 global confirmations
 confirmations+=1
 return actual_confirm(self,result)
class Input:
 buffer=io.BytesIO(raw)
output=io.StringIO()
with patch.object(sys,'stdin',Input()),redirect_stdout(output),patch.object(registration,'registration_admission',failed_exit),patch.object(RegistrationAdmission,'confirm',confirm),patch.object(registration,'add_collected_review_results',wraps=registration.add_collected_review_results) as writer:
 code=cli.main(['--repo',str(repo),'review','result','add',task,'--json'])
print(json.dumps(dict(code=code,response=json.loads(output.getvalue()),confirmations=confirmations,writer_calls=writer.call_count)))
'''


RECOVERY_LEASE_REFUSAL = r'''
import json,sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from task_governance_tool import review_finalization as f,review_handoff_originals
from task_governance_tool.review_answer_handoff import registration_admission
from task_governance_tool.review_session_transport import decode_submission
from task_governance_tool.session_identity import capture_caller_identity
repo,task=Path(sys.argv[2]),sys.argv[3]
service=f.Finalizer(repo,task,capture_caller_identity())
record=service.journal.read()
_,raw=review_handoff_originals.submission(repo,record.packet_path,record.result_paths)
with registration_admission(repo,decode_submission(raw),service.caller):
 with patch.object(service.core,'register',side_effect=AssertionError('competing registration')) as register:
  report=service.execute()
  assert register.call_count==0
print(json.dumps(report))
'''


class InstalledAnswerFinalizationTests(fixtures.PreparationFixture):
    git=fixtures.ReviewerMaterialTests.git
    committed_fixture=fixtures.ReviewerMaterialTests.committed_fixture
    finalize=old_finalization.InstalledFinalizationTests.finalize
    collect=collection_fixture.InstalledAnswerTests.collect

    def setUp(self):
        super().setUp()
        self.cli('setup','--review-answer-collection','on','--review-wait','off')

    def prepared(self):
        self.committed_fixture()
        task=self.task('Focused isolated verification')
        (self.root/'source.py').write_text('value = 2\n',encoding='utf-8')
        self.git('add','source.py')
        targeted=self.invoke('prepare-finalization','--repo',str(self.root),'--directory=reviews/g1',
            'target',task,'--kind=git_snapshot')
        self.assertEqual(targeted.returncode,0,targeted.stdout or targeted.stderr)
        target=json.loads(targeted.stdout)
        self.assertEqual(target['handoff'],{'status':'not_applicable'})
        prepared=self.invoke('prepare-finalization','--repo',str(self.root),'--directory=reviews/g1',
            'receipt',task,'--result=pass','--duration-ms=1','--scope-coverage=full',
            '--expected-target-generation='+str(target['source']['review_target']['generation']))
        self.assertEqual(prepared.returncode,0,prepared.stdout or prepared.stderr)
        context=json.loads(prepared.stdout)['handoff']
        self.assertEqual(context['finalization']['status'],'prepared')
        self.assertEqual(context['preparation']['route'],'integrated')
        self.assertEqual(context['answer_protocol'],'review-answer-v1')
        return task,context

    def ready(self,mode='collect-parent'):
        self.task_id,self.handoff=self.prepared()
        self.packet=self.handoff['packet_path']
        self.results=tuple(row['result_path'] for row in self.handoff['review_requests'])
        observed=self.collect(mode)
        self.assertEqual(observed['publications'],['saved','saved'])
        return {path:hashlib.sha256((self.root/path).read_bytes()).hexdigest() for path in self.results}

    def instrumented(self,mode):
        completed=subprocess.run([sys.executable,'-I','-S','-B','-c',FINALIZE,
            str(self.install.skill_root/'scripts'),str(self.root),self.task_id,mode],cwd=self.root,capture_output=True,check=False)
        self.assertEqual(completed.returncode,0,completed.stdout or completed.stderr)
        return json.loads(completed.stdout)

    def prewriter_failure(self, mode):
        completed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', PREWRITER_FAILURE,
            str(self.install.skill_root/'scripts'), str(self.root), self.task_id, mode],
            cwd=self.root, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout or completed.stderr)
        return json.loads(completed.stdout)

    def check_registration_observation(self, classification, states):
        def snapshot():
            return {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for directory in (self.root/'.taskgov', self.root/'reviews')
                for path in directory.rglob('*') if path.is_file()}
        before = snapshot()
        checked, report = self.finalize(self.task_id, check=True)
        self.assertEqual(checked.returncode, 0, report)
        observation = report['registration_observation']
        self.assertEqual(observation['classification'], classification, observation)
        self.assertEqual(observation['reason'], None)
        self.assertEqual(observation['native']['status'], 'absent')
        self.assertEqual(observation['native']['preparation_id'], observation['preparation_id'])
        self.assertEqual(observation['native']['submission_digest'], observation['submission_digest'])
        self.assertEqual([row['state'] for row in observation['sources']], states)
        self.assertEqual(snapshot(), before, 'check must not write or create state, originals or lease files')
        return report

    def test_busy_before_source_dispatch_preserves_or_recovers_exact_original_intent(self):
        originals = self.ready()
        before = self.git('rev-parse', 'HEAD')
        first = self.prewriter_failure('preflight')
        self.assertEqual(first['blocking_code'], 'database_busy', first)
        self.assertEqual(first['stages']['registration'], 'not_started')
        reserved = self.prewriter_failure('reserved')
        self.assertEqual(reserved['blocking_code'], 'database_busy', reserved)
        self.assertEqual(reserved['stages']['registration'], 'dispatching')
        self.assertEqual(reserved['observed_registered_receipt_ids'], [])
        self.assertEqual([row['state'] for row in self.collect('inspect')['states']], ['not_started']*2)
        report = self.check_registration_observation('not_dispatched', ['not_started']*2)
        self.assertEqual(report['stages']['registration'], 'dispatching')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assert_preserved(originals, report)

    def test_busy_after_source_dispatch_uses_settled_recovery_once(self):
        originals = self.ready()
        before = self.git('rev-parse', 'HEAD')
        first = self.prewriter_failure('dispatched')
        self.assertEqual(first['blocking_code'], 'database_busy', first)
        self.assertEqual(first['stages']['registration'], 'dispatching')
        self.assertEqual(first['observed_registered_receipt_ids'], [])
        self.assertEqual([row['state'] for row in self.collect('inspect')['states']], ['dispatched']*2)
        self.check_registration_observation('dispatched', ['dispatched']*2)
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assert_preserved(originals, report)
        receipt_ids = report['registered_receipt_ids']
        repeated, report = self.finalize(self.task_id)
        self.assertEqual(repeated.returncode, 0, report)
        self.assertEqual(report['registered_receipt_ids'], receipt_ids)
        self.assert_preserved(originals, report)

    def test_competing_source_lease_blocks_recovery_until_the_existing_writer_is_excluded(self):
        originals = self.ready()
        before = self.git('rev-parse', 'HEAD')
        failed = self.prewriter_failure('dispatched')
        self.assertEqual(failed['stages']['registration'], 'dispatching')
        held = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', RECOVERY_LEASE_REFUSAL,
            str(self.install.skill_root/'scripts'), str(self.root), self.task_id],
            cwd=self.root, capture_output=True, check=False)
        self.assertEqual(held.returncode, 0, held.stdout or held.stderr)
        report = json.loads(held.stdout)
        self.assertEqual(report['stages'], dict(registration='dispatching', commit='not_started', completion='not_started'))
        self.assertEqual(report['observed_registered_receipt_ids'], [])
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        completed, report = self.finalize(self.task_id)
        self.assertEqual(completed.returncode, 0, report)
        self.assert_preserved(originals, report)

    def assert_preserved(self,originals,report):
        self.assertEqual(report['status'],'completed',report)
        self.assertEqual(report['stages'],dict(registration='succeeded',commit='succeeded',completion='succeeded'))
        self.assertEqual(len(report['registered_receipt_ids']),2)
        for row in report['originals']:
            self.assertEqual(row['status'],'available',row)
            self.assertEqual(row['review']['checks'],['Complete assigned material inspected'])
            self.assertEqual(row['review']['limitations'],['Offline public host fixture'])
            self.assertEqual(row['review']['findings'],[])
            self.assertNotIn('provenance',row['review'])
        self.assertEqual({path:hashlib.sha256((self.root/path).read_bytes()).hexdigest() for path in self.results},originals)
        self.assertEqual(self.git('show','HEAD:source.py'),b'value = 2\n')
        shown=self.cli('task','show',self.task_id)
        self.assertEqual(shown['task']['status'],'done')
        self.assertEqual(shown['review_evidence']['counts']['receipts_current_generation'],2)

    def test_existing_finalizer_commits_and_completes_collected_answers_once(self):
        originals=self.ready()
        before=self.git('rev-parse','HEAD')
        checked,check=self.finalize(self.task_id,check=True)
        self.assertEqual(checked.returncode,0,check)
        self.assertEqual(check['stages'],dict(registration='not_started',commit='not_started',completion='not_started'))
        self.assertNotIn('registration_observation', check)
        self.assertEqual(self.git('rev-parse','HEAD'),before)
        completed,report=self.finalize(self.task_id)
        self.assertEqual(completed.returncode,0,report)
        self.assert_preserved(originals,report)
        commit=self.git('rev-parse','HEAD')
        for check_only in (True,False):
            repeated,second=self.finalize(self.task_id,check=check_only)
            self.assertEqual(repeated.returncode,0,second)
            self.assertEqual(second['commit_id'],report['commit_id'])
            self.assertNotIn('registration_observation', second)
            self.assert_preserved(originals,second)
            self.assertEqual(self.git('rev-parse','HEAD'),commit)

    def test_public_registration_keeps_success_when_admission_exit_fails(self):
        originals=self.ready()
        before=self.git('rev-parse','HEAD')
        completed=subprocess.run([sys.executable,'-I','-S','-B','-c',REGISTER_EXIT_FAILURE,
            str(self.install.skill_root/'scripts'),str(self.root),self.packet],
            cwd=self.root,capture_output=True,check=False)
        self.assertEqual(completed.returncode,0,completed.stdout or completed.stderr)
        observed=json.loads(completed.stdout)
        self.assertEqual((observed['code'],observed['confirmations'],observed['writer_calls']),(0,1,1))
        response=observed['response']
        self.assertTrue(response['ok'],response)
        self.assertEqual(response['errors'],[])
        self.assertEqual(len(response['data']['receipts']),2)
        self.assertEqual(response['warnings'],[{
            'code':'collection_registration_acknowledgement_unavailable',
            'message':'Review results were recorded. Collection acknowledgement is unresolved; do not resubmit.'}])
        self.assertNotIn(b'SENTINEL',completed.stdout+completed.stderr)
        state=self.collect('inspect')
        self.assertEqual([row['state'] for row in state['states']],['registered','registered'])
        audit=self.cli('task','show',self.task_id,'--audit',
            '--collection-preparation',state['preparation_id'],
            '--collection-submission',state['states'][0]['submission_digest'])['collection_registration']
        self.assertEqual(audit['status'],'recorded')
        self.assertEqual([row['receipt_id'] for row in audit['entries']],
            [row['receipt']['review_receipt_id'] for row in response['data']['receipts']])
        self.assertEqual(self.git('rev-parse','HEAD'),before)
        self.assertEqual({path:hashlib.sha256((self.root/path).read_bytes()).hexdigest() for path in self.results},originals)

    def test_lost_core_output_resumes_only_by_exact_public_inventory(self):
        originals=self.ready()
        before=self.git('rev-parse','HEAD')
        lost=self.instrumented('lose-registration-output')
        self.assertEqual(lost['registrations'],1)
        self.assertEqual(lost['lookups'],0)
        self.assertEqual(lost['report']['stages']['registration'],'dispatching')
        self.assertEqual(self.git('rev-parse','HEAD'),before)
        self.assertEqual(self.cli('task','show',self.task_id)['review_evidence']['counts']['receipts_current_generation'],2)
        resumed=self.instrumented('resume-exact')
        self.assertEqual(resumed['registrations'],0)
        self.assertEqual(resumed['lookups'],1)
        self.assert_preserved(originals,resumed['report'])
        repeated=self.instrumented('already-completed')
        self.assertEqual((repeated['registrations'],repeated['lookups']),(0,0))
        self.assert_preserved(originals,repeated['report'])

    def test_unable_registers_but_blocks_commit_and_completion(self):
        originals=self.ready('collect-unable')
        before=self.git('rev-parse','HEAD')
        completed,report=self.finalize(self.task_id)
        self.assertNotEqual(completed.returncode,0,report)
        self.assertEqual(report['blocking_code'],'review_unable',report)
        self.assertEqual(report['stages'],dict(registration='succeeded',commit='not_started',completion='not_started'))
        self.assertEqual(len(report['registered_receipt_ids']),2)
        self.assertEqual(self.git('rev-parse','HEAD'),before)
        shown=self.cli('task','show',self.task_id)
        self.assertEqual(shown['task']['status'],'review_pending')
        self.assertEqual(shown['review_evidence']['counts']['receipts_current_generation'],2)
        self.assertEqual(shown['review_evidence']['counts']['unable_current_generation'],1)
        readiness=self.cli('task','complete',self.task_id,'--verification-complete','--review-complete','--commit-not-required','--check','--read-only')
        self.assertEqual(readiness['blocking_codes'],['review_unable'],readiness)
        self.assertEqual({path:hashlib.sha256((self.root/path).read_bytes()).hexdigest() for path in self.results},originals)
