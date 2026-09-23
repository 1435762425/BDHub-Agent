import importlib.util
import json
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.market_send_canary import _binding_current,_dispatch_allowed,_received_conversation_id
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore
from lib.catalog_binding import offer_fingerprint
from lib.cycle_inbox import Inbox
from lib.operations_scheduler import _child_error,collecting_source_run
from lib.operations_scheduler import SubprocessStageExecutor
from lib.operations_workflow import create_run
from lib.workflow_resources import claim
from lib.template_library import DEFAULT_AGENT_SETTING,save_agent_setting
from lib.agent_reply_v2 import authorize_rollout,rollout_stage
from lib.continuous_send import _continuous_history_eligible
from lib.cycle_delivery import Deliveries
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('poll_market_inbox_cli',ROOT/'scripts/poll-market-inbox.py')
poll=importlib.util.module_from_spec(spec);spec.loader.exec_module(poll)
NOW=datetime(2026,9,23,17,0,tzinfo=timezone(timedelta(hours=8))).timestamp()

class MarketLaunchContracts(unittest.TestCase):
 def test_received_conversation_resumes_only_the_original_exact_receipt(self):
  intent={'request_ref':'original-ref','cid':'123','receipt':json.dumps({
   'requestRef':'original-ref','conversationId':'123','candidate':True})}
  self.assertEqual(_received_conversation_id(intent),'123')
  for changed in ({'requestRef':'replacement-ref'}, {'conversationId':'456'}, {'candidate':False}):
   invalid=intent|{'receipt':json.dumps(json.loads(intent['receipt'])|changed)}
   with self.assertRaisesRegex(CycleError,'market_send_conversation_result_unknown'):
    _received_conversation_id(invalid)

 def test_inbox_profile_contention_is_a_short_wait_not_an_attention_failure(self):
  Busy=type('ProfileBusyError',(Exception,),{})
  self.assertEqual(poll.failure_state(Busy()),'waiting_account')
  self.assertEqual(poll.failure_state(RuntimeError()),'attention')

 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir()
  with CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW) as store:store.plan('bjn-local-research','my')
  apply_database(self.root,'second-cycle',clock=lambda:NOW)
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW)
  self.plan=self.store.db.execute("SELECT id FROM plan WHERE market='my'").fetchone()[0]
 def tearDown(self):self.store.close();self.tmp.cleanup()

 def test_every_market_write_checks_saved_window_and_stop(self):
  self.store.db.execute("INSERT INTO continuous_send_control VALUES(?,0,1,0,'16:30','24:00','standard',1,?)",(self.plan,NOW))
  _dispatch_allowed(self.store,'my')
  self.store.db.execute("UPDATE continuous_send_control SET stop_requested=1 WHERE plan_id=?",(self.plan,))
  with self.assertRaisesRegex(CycleError,'continuous_send_stopped'):_dispatch_allowed(self.store,'my')
  self.store.db.execute("UPDATE continuous_send_control SET stop_requested=0,window_start='18:00' WHERE plan_id=?",(self.plan,))
  with self.assertRaisesRegex(CycleError,'outside_send_window'):_dispatch_allowed(self.store,'my')

 def test_current_binding_is_rechecked_after_claim(self):
  path=self.root/'var/catalog-links.sqlite';db=sqlite3.connect(path)
  db.execute('CREATE TABLE catalog_current_binding(market TEXT,catalog_source TEXT,pid TEXT,campaign_id TEXT,state TEXT,offer_fingerprint TEXT,list_id TEXT,card_payload TEXT)')
  offer={'catalogSource':'campaign','pid':'123','campaignId':'456','creatorPercent':'15','publicPercent':'10','totalPercent':'17'}
  card={'listId':'789'};candidate={'offer':offer,'card':card}
  db.execute('INSERT INTO catalog_current_binding VALUES(?,?,?,?,?,?,?,?)',
             ('my','campaign','123','456','active',offer_fingerprint(offer),'789',json.dumps(card)));db.commit()
  _binding_current(self.root,'my',candidate)
  db.execute("UPDATE catalog_current_binding SET state='inactive' WHERE pid='123'");db.commit()
  with self.assertRaisesRegex(CycleError,'card_binding_changed'):_binding_current(self.root,'my',candidate)
  db.close()

 def test_recent_inbox_and_old_checkpoint_share_capacity(self):
  Inbox(self.store)
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,0,0,1)",(self.plan,'creator-a','101'))
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,0,0,1)",(self.plan,'creator-b','102'))
  self.store.db.execute("INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,'tracking')",(self.plan,'2','102',NOW-1000,NOW-500))
  recent=[{'conversationId':'1','oecId':'101','conversationType':2}]
  self.assertEqual(poll._merge_targets(self.store,self.plan,recent,[],2),[('1','101'),('2','102')])
 def test_orphan_checkpoint_cannot_break_the_cold_inbox_scan(self):
  Inbox(self.store)
  self.store.db.execute("INSERT INTO inbox_checkpoint VALUES(?,?,?,?,?,'tracking')",
                        (self.plan,'999','999',NOW-1000,NOW-500))
  self.assertEqual(poll._merge_targets(self.store,self.plan,[],[],12),[])

 def test_source_resume_selects_original_scope_and_error_code_is_redacted(self):
  db=sqlite3.connect(self.root/'var/global-source.sqlite')
  db.execute('CREATE TABLE global_source_run(id TEXT,scope TEXT,state TEXT,created REAL)')
  db.execute('INSERT INTO global_source_run VALUES(?,?,?,?)',('original-run',json.dumps({'market':'it','account':'acc9'}),'collecting',NOW-100))
  db.commit();db.close()
  self.assertEqual(collecting_source_run(self.root,'it','acc9'),{'runId':'original-run','partitioned':False})
  self.assertIsNone(collecting_source_run(self.root,'it','acc6'))
  self.assertEqual(_child_error('private token=x\nGlobalSourceError: scan_already_collecting'),'scan_already_collecting')

 def test_stage_claim_records_actual_child_before_fixed_command_runs(self):
  (self.root/'.venv/bin').mkdir(parents=True)
  (self.root/'.venv/bin/python').symlink_to(sys.executable)
  (self.root/'scripts').mkdir()
  (self.root/'scripts/probe.py').write_text('import json; print(json.dumps({"state":"completed"}))\n')
  run=create_run(self.store,market='my',trigger_source='manual',scheduled_at=NOW,
                 request_id='stage-child-registration-001',sources=['campaign'])
  stage=next(row for row in run['stages'] if row['state']=='queued')
  ticket=claim(self.store,stage['stageRunId'],'scheduler-test-001',[('workflow:my',1)],
               worker_pid=os.getpid(),lease_seconds=300)
  executor=SubprocessStageExecutor(self.root)
  executor._claims.ticket=ticket|{'ownerId':'scheduler-test-001'}
  result=executor._call(['scripts/probe.py'],'child-registration',timeout=20)
  self.assertEqual(result['state'],'completed')
  child=self.store.db.execute('SELECT worker_pid FROM workflow_stage_claim WHERE stage_run_id=?',
                              (stage['stageRunId'],)).fetchone()[0]
  self.assertNotEqual(child,os.getpid())

 def test_market_agent_dispatches_one_frozen_text_and_checks_original_receipt(self):
  from lib.market_agent_reply import _window_open,run_reply
  setting=DEFAULT_AGENT_SETTING|{'enabled':True}
  self.assertFalse(_window_open(setting,NOW))
  self.assertTrue(_window_open(setting,NOW-2*3600))
  save_agent_setting(self.store,self.plan,0,{key:value for key,value in
      (DEFAULT_AGENT_SETTING|{'enabled':True}).items() if key not in ('revision','updatedAt')})
  reply={'id':'reply-1','plan_id':self.plan,'kind':'agent_generated_v2','state':'ready',
         'cid':'999','oec':'101','text':'Terima kasih!','request_ref':'01234567-89ab-4cde-8fab-0123456789ab'}
  marks=[]
  class Replies:
   def get(self,_id):return reply
   def begin(self,_id):reply['state']='inflight';return {'dispatchAllowed':True,'requestRef':reply['request_ref'],
      'stage':'send_message','componentKind':'text'}
   def accepted(self,_id,receipt):reply['state']='accepted';reply['receipt']=json.dumps(receipt)
   def confirm(self,_id,proof):reply['state']='confirmed'
   def unknown(self,_id):reply['state']='unknown'
  class Session:
   def conversation(self,cid,oec):return (cid,oec)
   def history_summary(self,*_args,**_kwargs):return {'contents':[]}
  class Adapter:
   def send_once(self,_conversation,text,ref,*,before_dispatch):
    result=before_dispatch({'market':'my','oecId':'101','conversationId':'999','componentKind':'text',
     'requestRef':ref,'textSha256':hashlib.sha256(text.encode()).hexdigest()})
    self_result=result
    assert self_result['dispatchAllowed']
    return {'requestRef':ref,'messageId':'123'}
   def readback(self,_conversation,_text,ref,*,message_id=None):
    return {'status':'confirmed','requestRef':ref,'conversationId':'999','messageId':'123','evidenceRef':'im-history:123'}
  @contextmanager
  def auth(_root,market,_report,*,canary,capability,stopped):
   self.assertEqual((market,canary,capability),('my',False,'agent_reply'))
   yield {'session':Session(),'adapter':Adapter(),'auth':object(),'account':type('A',(),{'name':'acc8'})()}
  @contextmanager
  def gate(*_args,**_kwargs):yield lambda:marks.append('write')
  with patch('lib.market_agent_reply.authenticated',auth),patch('lib.market_agent_reply.write_gate',gate),\
       patch('lib.market_agent_reply.Inbox'),patch('lib.market_agent_reply.Service'):
   result=run_reply(self.root,self.store,Replies(),reply,'my',authorized_now=True)
  self.assertEqual((result['state'],result['platformWrites'],result['realSends'],marks),
                   ('confirmed',1,1,['write']))

 def test_my_agent_requires_a_separate_first_send_page_event(self):
  save_agent_setting(self.store,self.plan,0,{key:value for key,value in
      (DEFAULT_AGENT_SETTING|{'enabled':True}).items() if key not in ('revision','updatedAt')})
  self.assertEqual(rollout_stage(self.store,self.plan,'my'),'pilot_required')
  started=authorize_rollout(self.store,self.plan,'my','pilot','my-agent-first-page-001')
  self.assertEqual(started['stage'],'pilot_running')
  self.assertEqual(rollout_stage(self.store,self.plan,'my'),'pilot_running')

 def test_card_and_text_need_two_remaining_unanswered_message_slots(self):
  stamps=[int((NOW-3*86400-i)*1000) for i in range(4)]
  history={'identityVerified':True,'hasMore':False,'senderCounts':{'ourMessages':4,'otherOrUnknown':0},
           'outboundCreateTimeRaw':stamps,'outboundTimeMissingCount':0}
  with self.assertRaisesRegex(CycleError,'recipient_message_limit'):
   _continuous_history_eligible(history,NOW,False,required_messages=2)
  _continuous_history_eligible(history,NOW,False,required_messages=1)

 def test_already_confirmed_card_settles_only_unsent_text_at_message_limit(self):
  deliveries=Deliveries(self.store);did='delivery-fixture-partial'
  self.store.db.execute("INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,'running')",
    (did,self.plan,'creator-x','101','123','source-1','{}',NOW-30,NOW+1800))
  self.store.db.execute("INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state,started) VALUES(?,?,'card-ref','confirmed',?)",
                        (did,'card',NOW-10))
  self.store.db.execute("INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state) VALUES(?,?,'text-ref','ready')",
                        (did,'text'))
  settled=deliveries.cancel_pending_text(did,'recipient_message_limit')
  self.assertEqual((settled['state'],[(p['kind'],p['state']) for p in settled['parts']]),
                   ('partial_delivery',[('card','confirmed'),('text','cancelled')]))
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_delivery_check WHERE delivery_id=?',(did,)).fetchone()[0],1)

if __name__=='__main__':unittest.main()
