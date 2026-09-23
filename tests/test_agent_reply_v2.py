import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from lib.agent_reply_v2 import (apply_production,authorize_rollout,generate, guide, production_context,
                                rollout_stage,save_guide,
                                simulation_context)
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError, CycleStore,encoded
from lib.cycle_auto_reply import AutoReplies
from lib.cycle_inbox import Inbox
from lib.template_library import DEFAULT_AGENT_SETTING,save_agent_setting


class AgentReplyV2Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
  with CycleStore(self.path/'var/second-cycle.sqlite') as store:self.plan=store.plan('bjn-local-research','it')
  apply_database(self.path,'second-cycle')
  self.store=CycleStore(self.path/'var/second-cycle.sqlite')
  Inbox(self.store);AutoReplies(self.store)
  self.store.db.execute('CREATE TABLE IF NOT EXISTS cycle_delivery(plan_id TEXT,state TEXT)')
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def decision(self,evidence='sim-1',route='reply',body='Ciao, grazie!'):
  return {'schemaVersion':'reply-decision-v2','route':route,'intentCodes':['collaboration_ack'],
          'evidenceMessageIds':[evidence],'replyText':body,'meaningZh':'同意合作',
          'reasonCode':'collaboration_ack','reasonSummaryZh':'明确表示愿意合作',
          'waitFor':'none','handoffReason':None}
 def test_same_simulation_uses_one_decision_and_never_touches_business_ledger(self):
  context=simulation_context('it','it-IT',[{'direction':'inbound','text':'Yes'}])
  calls=[]
  def model(messages,**_):calls.append(messages);return {'content':json.dumps(self.decision()),'usage':None}
  first=generate(ROOT,self.store,self.plan,'it',context,call=model)
  second=generate(ROOT,self.store,self.plan,'it',context,call=model)
  self.assertEqual(first['decisionId'],second['decisionId'])
  self.assertTrue(second['cached']);self.assertEqual(len(calls),1)
  self.assertIn('it-IT',calls[0][0]['content'])
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM service_reply').fetchone()[0],0)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM service_case').fetchone()[0],0)
 def test_simulation_carries_a_previous_contact_wait(self):
  context=simulation_context('it','it-IT',[{'direction':'inbound','text':'Va bene'}],'contact')
  self.assertEqual(context['previousWaitFor'],'contact')
  with self.assertRaisesRegex(CycleError,'agent_simulation_invalid'):
   simulation_context('it','it-IT',[{'direction':'inbound','text':'Va bene'}],'unknown')
 def test_decision_cannot_cite_unseen_message_or_emit_body_for_no_reply(self):
  context=simulation_context('it','it-IT',[{'direction':'inbound','text':'Grazie'}])
  with self.assertRaisesRegex(CycleError,'agent_decision_unresolved'):
   generate(ROOT,self.store,self.plan,'it',context,call=lambda *_args,**_kwargs:
            {'content':json.dumps(self.decision(evidence='unseen'))})
  self.assertEqual(self.store.db.execute("SELECT state FROM agent_reply_decision_v2").fetchone()[0],'unknown')
 def test_guide_revision_changes_prompt_without_rewriting_old_decision(self):
  before=guide(ROOT,self.store,self.plan)
  body=before['body']+'\n对达人保持简短友好。'
  after=save_guide(ROOT,self.store,self.plan,0,body)
  self.assertEqual(after['revision'],1)
  self.assertNotEqual(after['hash'],before['hash'])
  with self.assertRaisesRegex(CycleError,'agent_guide_revision_conflict'):
   save_guide(ROOT,self.store,self.plan,0,body)
 def test_replay_context_stops_before_future_turn(self):
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,0,1)",
                        (self.plan,'creator-1','123'))
  self.store.db.execute("INSERT INTO inbox_pending VALUES(?,?,1,1,'awaiting_content')",
                        (self.plan,'creator-1'))
  for mid,stamp in (('1',1000),('2',2000)):
   self.store.db.execute('INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
    ('turn-'+mid*24,self.plan,'creator-1','123','999',mid,'x'*64,'text',mid,int(stamp*1000),0,stamp))
  context=production_context(ROOT,self.store,self.plan,'it','turn-'+'1'*24)
  self.assertEqual([m['id'] for m in context['messages']],['1'])
 def live_context(self):
  now=self.store.clock();turn_id='turn-'+'a'*24
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,?,1)",
                        (self.plan,'creator-1','123',now+3600))
  self.store.db.execute("INSERT INTO inbox_pending VALUES(?,?,1,?,'awaiting_content')",
                        (self.plan,'creator-1',now-1))
  self.store.db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',
   (self.plan,'999','1','123','creatorReplies',int(now*1000),encoded({'kind':'creatorReplies'}),0,now))
  self.store.db.execute('INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
   (turn_id,self.plan,'creator-1','123','999','1','a'*64,'text','Certo',int(now*1000),0,now))
  AutoReplies(self.store).service.capture(self.plan,'999','123',[{'messageId':'1','format':'text',
   'text':'Certo','nativeType':'text','rawSha256':'a'*64}])
  return production_context(ROOT,self.store,self.plan,'it',turn_id)
 def generated(self,context,route='reply',body='Perfetto, grazie!'):
  raw=self.decision(evidence='1',route=route,body=None if route=='no_reply' else body)
  if route=='handoff':raw['handoffReason']='需要人工处理'
  if route=='request_detail':raw['waitFor']='contact'
  return generate(ROOT,self.store,self.plan,'it',context,mode='production',
   call=lambda *_args,**_kwargs:{'content':json.dumps(raw)})
 def confirm_local(self,reply_id):
  replies=AutoReplies(self.store);replies.enable(self.plan,'test-only')
  q=replies.get(reply_id);permit=replies.begin(reply_id)
  self.assertTrue(permit['dispatchAllowed'])
  replies.accepted(reply_id,{'requestRef':q['request_ref']})
  replies.confirm(reply_id,{'status':'confirmed','requestRef':q['request_ref'],
   'conversationId':'999','messageId':'confirmed-1'})
 def test_generated_reply_freezes_body_and_completes_only_after_receipt(self):
  context=self.live_context();generated=self.generated(context)
  result=apply_production(self.store,self.plan,context,generated)
  reply_id=result['replyId'];q=AutoReplies(self.store).get(reply_id)
  self.assertEqual(q['kind'],'agent_generated_v2')
  self.assertEqual(q['text'],'Perfetto, grazie!')
  self.assertEqual(self.store.db.execute('SELECT state FROM inbox_pending').fetchone()[0],'awaiting_content')
  self.confirm_local(reply_id)
  self.assertEqual(self.store.db.execute('SELECT state FROM inbox_pending').fetchone()[0],'answered')
 def test_operator_backend_reply_invalidates_an_unsubmitted_ai_reply(self):
  context=self.live_context();generated=self.generated(context)
  reply_id=apply_production(self.store,self.plan,context,generated)['replyId']
  replies=AutoReplies(self.store);replies.enable(self.plan,'test-only')
  now=self.store.clock()+1
  event={'messageId':'2','kind':'ourMessages','createTimeRaw':int(now*1000),
         'messageType':1000,'conversationId':'999','oecId':'123'}
  Inbox(self.store).ingest(self.plan,'999','123',{'identityVerified':True,'hasMore':False,'events':[event]})
  replies.service.capture(self.plan,'999','123',[{'messageId':'2','format':'text',
   'text':'I have already answered this question.','nativeType':'text','rawSha256':'b'*64}])
  with self.assertRaisesRegex(CycleError,'reply_context_changed'):replies.begin(reply_id)
  self.assertEqual(replies.get(reply_id)['state'],'ready')
 def test_contact_request_keeps_creator_out_of_outreach_after_receipt(self):
  context=self.live_context();generated=self.generated(context,'request_detail','Mi mandi il contatto WhatsApp?')
  reply_id=apply_production(self.store,self.plan,context,generated)['replyId']
  self.confirm_local(reply_id)
  self.assertEqual(self.store.db.execute('SELECT state FROM inbox_pending').fetchone()[0],'waiting_contact')
 def test_polite_ack_without_contact_keeps_the_original_wait(self):
  context=self.live_context();generated=self.generated(context,'request_detail','Mi mandi il contatto WhatsApp?')
  reply_id=apply_production(self.store,self.plan,context,generated)['replyId']
  self.confirm_local(reply_id)
  now=self.store.clock()+1
  self.store.db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',
   (self.plan,'999','2','123','creatorReplies',int(now*1000),encoded({'kind':'creatorReplies'}),0,now))
  self.store.db.execute('INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
   ('turn-'+'b'*24,self.plan,'creator-1','123','999','2','b'*64,'text','Va bene',int(now*1000),0,now))
  AutoReplies(self.store).service.capture(self.plan,'999','123',[{'messageId':'2','format':'text',
   'text':'Va bene','nativeType':'text','rawSha256':'b'*64}])
  self.store.db.execute("UPDATE inbox_pending SET revision=2,state='awaiting_content'")
  self.store.db.execute('UPDATE relationship SET revision=revision+1')
  current=production_context(ROOT,self.store,self.plan,'it','turn-'+'b'*24)
  self.assertEqual(current['previousWaitFor'],'contact')
  raw=self.decision(evidence='2',route='no_reply',body=None)
  raw['waitFor']='contact'
  followup=generate(ROOT,self.store,self.plan,'it',current,mode='production',
   call=lambda *_args,**_kwargs:{'content':json.dumps(raw)})
  self.assertIsNone(apply_production(self.store,self.plan,current,followup)['replyId'])
  self.assertEqual(self.store.db.execute('SELECT state FROM inbox_pending').fetchone()[0],'waiting_contact')
 def test_handoff_locks_creator_before_ack_and_keeps_it_locked(self):
  context=self.live_context();generated=self.generated(context,'handoff','Ricevuto, ti rispondiamo.')
  reply_id=apply_production(self.store,self.plan,context,generated)['replyId']
  self.assertEqual(self.store.db.execute('SELECT mode FROM relationship').fetchone()[0],'human')
  self.assertEqual(self.store.db.execute('SELECT state FROM service_case').fetchone()[0],'open')
  self.confirm_local(reply_id)
  self.assertEqual(self.store.db.execute('SELECT mode FROM relationship').fetchone()[0],'human')
  self.assertEqual(self.store.db.execute('SELECT ack_state FROM service_case').fetchone()[0],'confirmed')
 def test_newer_inbound_revision_invalidates_unsent_decision(self):
  context=self.live_context();generated=self.generated(context)
  self.store.db.execute('UPDATE inbox_pending SET revision=2')
  with self.assertRaisesRegex(CycleError,'agent_context_changed'):
   apply_production(self.store,self.plan,context,generated)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM service_reply').fetchone()[0],0)
 def test_first_real_send_needs_a_page_event_and_then_waits_for_resume(self):
  self.assertEqual(rollout_stage(self.store,self.plan,'it'),'pilot_required')
  with self.assertRaisesRegex(CycleError,'agent_rollout_setting_disabled'):
   authorize_rollout(self.store,self.plan,'it','pilot','pilot-request-0001')
  setting={key:value for key,value in DEFAULT_AGENT_SETTING.items() if key not in ('revision','updatedAt')}
  save_agent_setting(self.store,self.plan,0,{**setting,'enabled':True})
  self.assertEqual(authorize_rollout(self.store,self.plan,'it','pilot','pilot-request-0001')['stage'],
                   'pilot_running')
  self.assertTrue(authorize_rollout(self.store,self.plan,'it','pilot','pilot-request-0001')['duplicate'])
  with self.assertRaisesRegex(CycleError,'agent_rollout_state_changed'):
   authorize_rollout(self.store,self.plan,'it','full','resume-request-0001')
  self.store.db.execute("INSERT INTO service_reply(id,plan_id,creator_id,pending_revision,oec,cid,kind,case_id,text,context_hash,state,request_ref,created) VALUES('r',?,'c',1,'o','1','agent_generated_v2',NULL,'reply','hash','confirmed','ref',1)",(self.plan,))
  self.assertEqual(rollout_stage(self.store,self.plan,'it'),'pilot_complete')
  self.assertEqual(authorize_rollout(self.store,self.plan,'it','full','resume-request-0001')['stage'],
                   'full')


if __name__=='__main__':unittest.main()
