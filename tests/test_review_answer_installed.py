"""Physical installed package: issued request -> public observation -> writer.

Host pages are deterministic offline fixtures. Preparation, resolver, physical
originals, operational journal, public CLI and core database are real.
"""
from __future__ import annotations
import json
import subprocess
import sys

from tests import test_review_handoff_preparation as fixtures


COLLECT = r'''
import io,json,sys,sqlite3
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from task_governance_tool.review_answer_preparation import locate
from task_governance_tool.review_answer_handoff import service_for,initial_submission
from task_governance_tool.review_answer_host import PublicAnswerHost
from task_governance_tool.review_answer_values import basis_digest
from task_governance_tool.review_answer import AnswerError
from task_governance_tool import review_handoff_transport as files
repo,packet_path,mode=Path(sys.argv[2]),sys.argv[3],sys.argv[4]
packet_raw=(repo/packet_path).read_bytes();packet=files.decode_packet(packet_raw)
if packet.get('review_session_context',{}).get('version')==2:
 if mode in ('collect-parent-rejected','inspect'):
  # Fixture-only lookup lets the public entry itself encounter corrupt state.
  from contextlib import closing
  from task_governance_tool.review_handoff_scope import resolve
  from task_governance_tool.review_preparation_repository import PreparationRepository
  from task_governance_tool.review_wait_basis_repository import read_review_basis
  from task_governance_tool.storage import connect_initialized_readonly
  from task_governance_tool.review_answer_collection_repository import CollectionRepository
  resolution=resolve(repo,writing=False)
  with closing(connect_initialized_readonly(resolution.target)) as connection:
   basis=read_review_basis(connection,project_id=resolution.target.project.project_id,task_id=packet['task']['task_id'])
  _,root=PreparationRepository(resolution.paths.review_preparation_store(basis['parent_session_id'],basis['task_id'],basis['target_generation'])).read()
  collection_bases=tuple(row['basis'] for row in CollectionRepository(resolution.paths.review_answer_collection_store(basis['parent_session_id'],basis['task_id'],basis['target_generation'],root.preparation_id,packet['review_session_context']['continuation_id'])).read())
 else:
  from task_governance_tool.review_answer_continuation_preparation import locate_continuation
  resolution,root,collection_bases=locate_continuation(repo,packet_path,packet_raw,packet)
else:
 resolution,root=locate(repo,packet_path,packet_raw,packet)
 collection_bases=root.collection_bases
service=service_for(repo,resolution,root,collection_bases[0])
result_paths=service.original_paths
preparation_path=resolution.paths.review_preparation_store(root.basis['parent_session_id'],root.basis['task_id'],root.basis['target_generation'])
parent=root.basis['parent_session_id'];parent_turn='90000000-0000-4000-8000-000000000001'
def child(slot):return '90000000-0000-4000-8000-'+format(1000+slot,'012x')
def turn(slot):return '90000000-0000-4000-8000-'+format(2000+slot,'012x')
def answer(slot):
 return (' \r\n```json\r\n'+json.dumps(dict(verdict='unable_to_review' if mode=='collect-unable' and slot==1 else 'pass',summary='Installed review '+str(slot)+' 日本語の原本',findings=[],checks=['Complete assigned material inspected'],limitations=['Offline public host fixture']),ensure_ascii=False,separators=(',',':'))+'\r\n```\r\n').encode('utf-8')
def page(thread,limit,tid,items,status='completed'):
 return dict(schemaVersion=1,thread=dict(id=thread,kind='codex',hostId='local'),page=dict(order='newest_first',limit=limit,hasMore=False,nextCursor=None),turns=[dict(id=tid,status=status,items=items,error=None,startedAt=1,completedAt=2 if status=='completed' else None,durationMs=1 if status=='completed' else None)])
class Host:
 def __init__(self,metadata=None):self._metadata=metadata
 def _json_call(self,method,args):
  assert method=='read_thread' and args['includeOutputs'] is False and args['maxOutputCharsPerItem']==256
  thread,limit=args['threadId'],args['turnLimit']
  if thread==parent:
   return page(parent,1,parent_turn,[dict(type='subAgentActivity',id='dispatch-'+str(s),kind='started',agentThreadId=child(s)) for s in range(1,len(result_paths)+1)])
  slot=next(s for s in range(1,len(result_paths)+1) if child(s)==thread)
  assert not(mode=='finish-save' and slot==1 and limit==2),'recovery re-fetched uncertain original'
  return page(thread,limit,turn(slot),[] if limit==1 else [dict(type='agentMessage',id='message-'+str(slot),phase='final_answer',text=answer(slot).decode())],status='inProgress' if limit==1 else 'completed')
host=PublicAnswerHost(Host(),parent,parent_turn)
def bind(slot):
 observed=host.bind_dispatch('dispatch-'+str(slot),child(slot),creation_context=True)
 assignment=dict(kind='independent',review_profiles=['general'],review_lenses=['correctness'],requested_model=None,requested_effort=None,requested_skill=None,context_relation=None)
 binding=dict(basis_digest=basis_digest(collection_bases[slot-1]),slot=slot,reviewer_key='installed-reviewer-'+str(slot),dispatch_id='dispatch-'+str(slot),parent_session_id=parent,reviewer_session_id=child(slot),initial_turn_id=observed['initial_turn_id'],binding_source=observed['source'],assignment=assignment)
 service.bind(slot,binding)
 return binding
def observe(slot,failed=False):
 binding=bind(slot)
 observation=host.observe_answer(basis_digest=binding['basis_digest'],slot=slot,attempt=0,reviewer_session_id=child(slot),turn_id=turn(slot),expected_message_id=None)
 if failed:
  write=files.write_exclusive
  def interrupted(path,raw):
   write(path,raw)
   raise OSError('isolated acknowledgement interruption')
  try:
   with patch.object(files,'write_exclusive',side_effect=interrupted):service.observe(slot,observation)
  except AnswerError as error:assert error.failure['class']=='save_unknown'
 else:service.observe(slot,observation)
if mode in ('collect-parent','collect-unable','finish-save','collect-parent-rejected'):
 from task_governance_tool.review_wait_runtime.project_server import ProjectConfig,ProjectReviewWaitSession
 from task_governance_tool.review_wait_runtime.review_wait_server import _call
 session=ProjectReviewWaitSession(ProjectConfig(repo,Path(sys.argv[1])/'review_wait_host.mjs',repo,'UTC'),managed_host_factory=lambda **kwargs:Host(kwargs['metadata']),session_factory=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('worker path invoked')))
 write=files.write_exclusive
 def publish(path,raw):
  assert not(mode=='finish-save' and path==repo/result_paths[0]),'recovery repeated publication'
  return write(path,raw)
 with patch.object(files,'write_exclusive',side_effect=publish):
  response=_call(dict(name='review_answer_collect',arguments=dict(task_id=root.basis['task_id'],reviewer_ids=[child(slot) for slot in range(1,len(result_paths)+1)]),_meta=dict(threadId=parent,turnId=parent_turn)),session)
 collected=json.loads(response['content'][0]['text'])
 if mode=='collect-parent-rejected':
  assert collected.get('ok') is False,collected
 else:
  assert collected.get('ok') is True,collected
  assert collected['collected']==list(range(1,len(result_paths)+1)) and collected['unavailable']==[]
elif mode=='collect':
 for slot in range(1,len(result_paths)+1):observe(slot)
elif mode=='bind-continuation':
 # An independently admitted follow-up binding is an offline fixture seam.
 # The real host adapter cannot create this binding and is not certified here.
 assert collection_bases[0]['continuation'] is not None
 for slot in range(1,len(result_paths)+1):bind(slot)
elif mode=='corrupt-basis':
 from task_governance_tool.review_answer_collection_repository import _encode
 record=service.repository.read()[0]
 record['basis']['request_digest']='sha256:'+'f'*64
 if record['binding'] is not None:record['binding']['basis_digest']=basis_digest(record['basis'])
 with sqlite3.connect(service.repository.path) as connection:
  connection.execute('UPDATE slots SET record_json=? WHERE slot=1',(_encode(record),))
elif mode=='fail-save':observe(1,True)
elif mode=='readback-fails':
 try:service.readback(1)
 except AnswerError:pass
 else:raise AssertionError('unknown residue accepted implicitly')
elif mode=='drop-ack':
 from task_governance_tool import cli
 from task_governance_tool.review_answer_handoff import RegistrationAdmission
 task,raw=initial_submission(repo,root,resolution,root.result_paths)
 class Input:
  buffer=io.BytesIO(raw)
 sys.stdin=Input()
 with patch.object(RegistrationAdmission,'confirm',side_effect=OSError('isolated lost acknowledgement')):
  result=cli.main(['--repo',str(repo),'review','result','add',task,'--json'])
 assert result==0
 sys.exit(0)
elif mode=='confirm':
 entries=json.loads(sys.argv[5])['entries']
 for entry in entries:
  record=service.repository.read()[entry['slot']-1]
  attempt=record['attempts'][0]
  assert entry['reviewer_session_id']==record['binding']['reviewer_session_id'] and entry['turn_id']==attempt['turn_id'] and entry['message_id']==attempt['source_identity']['message_id']
  assert entry['original_digest']==attempt['observed_digest'] and entry['submission_digest']==record['registration']['submission_digest']
  service.confirm_registration(entry['slot'],entry['submission_digest'],entry['receipt_id'])
elif mode!='inspect':raise AssertionError('unknown fixture action')
records=service.repository.read()
print(json.dumps(dict(preparation_id=root.preparation_id,states=[r['registration'] for r in records],publications=[r['attempts'][0]['publication']['state'] if r['attempts'][0]['publication'] else None for r in records],digests=[r['attempts'][0]['observed_digest'] for r in records],body_retained='日本語の原本'.encode() in service.repository.path.read_bytes(),journal_digest=sha256(service.repository.path.read_bytes()).hexdigest(),preparation_digest=sha256(preparation_path.read_bytes()).hexdigest()),sort_keys=True))
'''


class InstalledAnswerTests(fixtures.PreparationFixture):
    def setUp(self):
        super().setUp()
        self.cli('setup','--review-answer-collection','on')
        self.cli('setup','--review-wait','off')
        self.task_id=self.task()
        completed,prepared=self.prepare(self.task_id)
        self.assertEqual(completed.returncode,0,completed.stdout or completed.stderr)
        self.handoff=prepared['handoff']
        self.packet=self.handoff['packet_path']
        self.results=tuple(row['result_path'] for row in self.handoff['review_requests'])

    def collect(self,mode,extra=None):
        command=[sys.executable,'-I','-S','-B','-c',COLLECT,str(self.install.skill_root/'scripts'),str(self.root),self.packet,mode]
        if extra is not None:command.append(json.dumps(extra))
        completed=subprocess.run(command,cwd=self.root,capture_output=True,check=False)
        self.assertEqual(completed.returncode,0,completed.stdout or completed.stderr)
        return json.loads(completed.stdout)

    def audit(self,state):
        digest=state['states'][0]['submission_digest']
        return self.cli('task','show',self.task_id,'--audit','--collection-preparation',state['preparation_id'],'--collection-submission',digest)['collection_registration']

    def continue_preparation(self):
        selection=dict(version=1,reviewers=[dict(session_id='90000000-0000-4000-8000-'+format(1000+slot,'012x'),
            action='supplement',prior_receipt_id=None,reason='Recover missing assigned review material') for slot in (1,2)])
        completed=self.invoke('continue','--repo',str(self.root),'--task-id',self.task_id,
            '--target-generation','1','--directory','reviews/continued',raw=json.dumps(selection).encode())
        self.assertEqual(completed.returncode,0,completed.stdout or completed.stderr)
        self.handoff=json.loads(completed.stdout)['handoff']
        self.packet=self.handoff['packet_path']
        self.results=tuple(row['result_path'] for row in self.handoff['review_requests'])
        self.collect('bind-continuation')

    def assert_corrupt_basis_rejected(self, *, saved):
        if saved:
            self.collect('collect-parent')
        issued=self.collect('inspect')
        corrupt=self.collect('corrupt-basis')
        self.assertEqual(corrupt['preparation_digest'],issued['preparation_digest'])
        before=self.install.db_path.read_bytes()
        rejected=self.collect('collect-parent-rejected')
        self.assertEqual(rejected,corrupt)
        submitted=self.invoke('submit','--repo',str(self.root),'--packet',self.packet,'--',*self.results)
        self.assertNotEqual(submitted.returncode,0,submitted.stdout or submitted.stderr)
        self.assertEqual(self.collect('inspect'),corrupt)
        self.assertEqual(self.install.db_path.read_bytes(),before)
        self.assertEqual(self.cli('task','show',self.task_id)['review_evidence']['counts']['receipts_current_generation'],0)
        self.assertEqual([state['state'] for state in corrupt['states']],['not_started','not_started'])
        if not saved:
            self.assertTrue(all(not (self.root/path).exists() for path in self.results))

    def test_unissued_initial_basis_rejected_before_binding_or_publication(self):
        self.assert_corrupt_basis_rejected(saved=False)

    def test_unissued_initial_basis_rejected_by_public_submit_after_saved_answer(self):
        self.assert_corrupt_basis_rejected(saved=True)

    def test_unissued_continuation_basis_rejected_before_binding_or_publication(self):
        self.continue_preparation()
        self.assert_corrupt_basis_rejected(saved=False)

    def test_unissued_continuation_basis_rejected_by_public_submit_after_saved_answer(self):
        self.continue_preparation()
        self.assert_corrupt_basis_rejected(saved=True)

    def test_issued_continuation_collects_and_submits_with_pinned_basis(self):
        self.continue_preparation()
        observed=self.collect('collect-parent')
        self.assertEqual(observed['publications'],['saved','saved'])
        submitted=self.invoke('submit','--repo',str(self.root),'--packet',self.packet,'--',*self.results)
        self.assertEqual(submitted.returncode,0,submitted.stdout or submitted.stderr)
        self.assertEqual(self.cli('task','show',self.task_id)['review_evidence']['counts']['receipts_current_generation'],2)

    def test_issued_five_field_read_exact_collection_submit_and_public_reconciliation(self):
        request=self.handoff['review_requests'][0]['request']
        self.assertIn('verdict, summary, findings, checks, limitations',request)
        self.assertNotIn('Save command:',request)
        self.assertEqual(request.count('review_wait_inspect'),1)
        self.assertIn('once with {"task_id":"'+self.task_id+'"}',request)
        read=self.invoke('read','--repo',str(self.root),'--packet',self.packet,reviewer=0)
        self.assertEqual(read.returncode,0,read.stdout or read.stderr)
        view=json.loads(read.stdout)
        self.assertEqual(set(view['result_template']),{'verdict','summary','findings','checks','limitations'})
        self.assertNotIn('review_wait_inspect',' '.join(view['result_instructions']))
        observed=self.collect('collect-parent')
        self.assertEqual(observed['publications'],['saved','saved'])
        self.assertFalse(observed['body_retained'])
        submitted=self.invoke('submit','--repo',str(self.root),'--packet',self.packet,'--',*self.results)
        self.assertEqual(submitted.returncode,0,submitted.stdout or submitted.stderr)
        state=self.collect('inspect')
        self.assertEqual([s['state'] for s in state['states']],['registered','registered'])
        registered=self.audit(state)
        self.assertEqual(registered['status'],'recorded')
        self.assertTrue(registered['current_basis'])
        self.assertEqual([e['original_digest'] for e in registered['entries']],observed['digests'])
        self.assertEqual(self.cli('task','show',self.task_id)['task']['status'],'review_pending')
        rejected=self.invoke('submit','--repo',str(self.root),'--packet',self.packet,'--',*self.results)
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(self.audit(state),registered)

    def test_failed_save_restart_and_lost_registration_acknowledgement_do_not_replay(self):
        failed=self.collect('fail-save')
        self.assertEqual(failed['publications'],['unknown',None])
        self.collect('readback-fails')
        self.assertEqual(self.collect('inspect')['publications'],['unknown',None])
        recovered=self.collect('finish-save')
        self.assertEqual(recovered['publications'],['saved','saved'])
        response=self.collect('drop-ack')
        self.assertTrue(response['ok'])
        state=self.collect('inspect')
        self.assertEqual([s['state'] for s in state['states']],['dispatched','dispatched'])
        registered=self.audit(state)
        self.assertEqual(registered['status'],'recorded')
        confirmed=self.collect('confirm',registered)
        self.assertEqual([s['state'] for s in confirmed['states']],['registered','registered'])
        self.assertEqual(confirmed['digests'],recovered['digests'])
