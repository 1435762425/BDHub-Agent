import importlib.util,json,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib.schema_migrations import apply_database
from lib.cycle_inbox import Inbox
from lib.cycle_auto_reply import AutoReplies
from lib.reply_events import backfill
from lib.reply_scope import unresolved,freeze
from lib.agent_reply_v2 import production_context,generate,apply_production
spec=importlib.util.spec_from_file_location('scope_worker',ROOT/'scripts/run-agent-replies.py');worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)

class ReplyScopeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=[time.time()-1000]
  self.s=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now[0]);self.plan=self.s.plan('bjn-local-research','it')
  apply_database(self.root,'second-cycle');self.inbox=Inbox(self.s);self.replies=AutoReplies(self.s);self.replies.enable(self.plan,'test')
  # Real delivery schema is initialized normally, not a separate fixture contract.
  from lib.cycle_delivery import Deliveries
  Deliveries(self.s)
  self.seed('c','123','999')
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def seed(self,creator,oec,cid,plan=None):
  plan=plan or self.plan
  self.s.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,0,1)",(plan,creator,oec))
  self.inbox.ingest(plan,cid,oec,{'identityVerified':True,'hasMore':False,'events':[]})
 def add(self,mid,text,*,creator='c',oec='123',cid='999',plan=None,kind='creatorReplies',at=None):
  plan=plan or self.plan;self.now[0]+=2
  event={'messageId':mid,'kind':kind,'conversationId':cid,'oecId':oec,'createTimeRaw':int((at or self.now[0])*1000)}
  self.inbox.ingest(plan,cid,oec,{'identityVerified':True,'hasMore':False,'events':[event]})
  self.replies.service.capture(plan,cid,oec,[{'messageId':mid,'format':'text','text':text,'nativeType':'text','rawSha256':digest(text)}]);backfill(self.s)
 def context(self,plan=None,creator='c',market='it'):
  plan=plan or self.plan;rows=unresolved(self.s,plan,creator)
  return production_context(ROOT,self.s,plan,market,rows[-1]['turnId'],live=True)
 def generate(self,ctx,route='reply'):
  d={'schemaVersion':'reply-decision-v2','route':route,'intentCodes':['collaboration_ack'],'evidenceMessageIds':[ctx['unansweredMessageIds'][0]],'replyText':None if route=='no_reply' else 'Grazie, ecco le risposte.','meaningZh':'回答全部问题','reasonCode':'collaboration_ack','reasonSummaryZh':'按当前问题回答','waitFor':'none','handoffReason':None}
  return generate(ROOT,self.s,ctx['replyScope'] and self.s.db.execute('SELECT plan_id FROM inbound_turn WHERE turn_id=?',(ctx['turnId'],)).fetchone()[0],ctx['market'],ctx,mode='production',call=lambda *a,**k:{'content':json.dumps(d)})
 def prepare(self,ctx=None):
  ctx=ctx or self.context();return apply_production(self.s,self.plan,ctx,self.generate(ctx))['replyId']
 def confirm(self,rid):
  q=self.replies.get(rid);self.replies.confirm(rid,{'status':'confirmed','requestRef':q['request_ref'],'conversationId':q['cid'],'messageId':'9000'})
 def test_four_market_scope_contains_all_questions_then_thanks(self):
  for i,market in enumerate(('it','br','my','uk')):
   plan=self.plan if market=='it' else self.s.plan('bjn-local-research',market)
   if market!='it':self.seed('c','123','999',plan)
   for mid,body in (('1','First question?'),('2','Second question?'),('3','Thanks')):self.add(mid,body,plan=plan)
   ctx=self.context(plan,market=market)
   self.assertEqual(ctx['unansweredMessageIds'],['1','2','3']);self.assertEqual(len(ctx['replyScope']),3)
   q=apply_production(self.s,plan,ctx,self.generate(ctx))['replyId'];self.replies.enable(plan,'test');self.replies.begin(q);self.confirm(q)
   self.assertEqual(unresolved(self.s,plan,'c'),[])
 def test_new_messages_keep_original_queue_age(self):
  self.seed('d','456','888');self.add('1','old question');first=unresolved(self.s,self.plan,'c')[0]['at']
  self.add('2','another creator',creator='d',oec='456',cid='888');self.add('3','more details')
  rows=worker.pending_rows(self.s,self.plan,self.now[0]);self.assertEqual([r['creator_id'] for r in rows],['c','d'])
  self.assertEqual(unresolved(self.s,self.plan,'c')[0]['at'],first)
 def test_new_inbound_during_generation_refuses_stale_apply(self):
  self.add('1','question');ctx=self.context();g=self.generate(ctx);self.add('2','new question')
  with self.assertRaisesRegex(CycleError,'agent_context_changed'):apply_production(self.s,self.plan,ctx,g)
  self.assertEqual(self.s.db.execute('select count(*) from service_reply').fetchone()[0],0)
 def test_new_inbound_after_prepare_refuses_begin(self):
  self.add('1','question');q=self.prepare();self.add('2','new question')
  with self.assertRaisesRegex(CycleError,'reply_context_changed'):self.replies.begin(q)
  self.assertEqual(self.replies.get(q)['state'],'ready')
 def test_new_inbound_while_submitted_only_old_scope_settles(self):
  self.add('1','old question');q=self.prepare();self.replies.begin(q);self.add('2','new question');self.replies.unknown(q)
  self.assertEqual(len(unresolved(self.s,self.plan,'c')),2);self.confirm(q);self.confirm(q)
  self.assertEqual([r['messageId'] for r in unresolved(self.s,self.plan,'c')],['2'])
  self.assertEqual(self.s.db.execute('select state from inbox_pending').fetchone()[0],'awaiting_classification')
  self.assertGreater(self.s.db.execute('select inbox_until from relationship').fetchone()[0],self.now[0])
  self.assertEqual(self.context()['unansweredMessageIds'],['2'])
 def test_edited_old_message_keeps_new_version_without_reanswering_other_covered(self):
  self.add('1','first');self.add('2','second');q=self.prepare();self.replies.begin(q);self.now[0]+=2
  self.replies.service.capture(self.plan,'999','123',[{'messageId':'1','format':'text','text':'edited first','nativeType':'text','rawSha256':digest('edited')}]);backfill(self.s)
  self.confirm(q);remaining=unresolved(self.s,self.plan,'c')
  self.assertEqual([(r['messageId'],r['text']) for r in remaining],[('1','edited first')])
  q2=self.prepare();self.replies.begin(q2);self.confirm(q2);self.assertEqual(unresolved(self.s,self.plan,'c'),[])
 def test_manual_answer_suppresses_only_preceding_questions(self):
  self.add('1','old question');self.add('2','answered',kind='ourMessages');self.add('3','new question')
  self.assertEqual(self.context()['unansweredMessageIds'],['3'])
 def test_manual_reply_during_generation_invalidates_scope_without_pending_revision_change(self):
  self.add('1','question');ctx=self.context();g=self.generate(ctx);self.add('2','answered',kind='ourMessages')
  with self.assertRaisesRegex(CycleError,'reply_context_changed'):apply_production(self.s,self.plan,ctx,g)
 def test_confirm_never_undoes_human_control(self):
  self.add('1','question');q=self.prepare();self.replies.begin(q)
  self.s.db.execute("UPDATE relationship SET mode='human',revision=revision+1")
  self.s.db.execute("UPDATE inbox_pending SET state='human'");self.confirm(q)
  self.assertEqual(self.s.db.execute('select mode from relationship').fetchone()[0],'human')
  self.assertEqual(self.s.db.execute('select state from inbox_pending').fetchone()[0],'human')
 def test_no_reply_settles_frozen_scope_once_and_new_wait_starts_fresh(self):
  self.add('1','thanks');ctx=self.context();g=self.generate(ctx,'no_reply');apply_production(self.s,self.plan,ctx,g)
  self.assertEqual(unresolved(self.s,self.plan,'c'),[])
  with self.assertRaises(CycleError):apply_production(self.s,self.plan,ctx,g)
  self.add('2','new question');self.assertEqual(self.context()['unansweredMessageIds'],['2'])
 def test_more_than_24_questions_are_not_silently_dropped(self):
  for i in range(1,31):self.add(str(i),'Question '+str(i))
  ctx=self.context();self.assertEqual(len(ctx['unansweredMessageIds']),30)
  self.assertEqual(sum(bool(m.get('unanswered')) for m in ctx['messages']),30)
 def test_oversize_scope_does_not_call_model_or_consume_attempt(self):
  self.add('1','问'*3000);self.add('2','问'*3000);ctx=self.context();calls=[]
  for _ in range(2):
   with self.assertRaisesRegex(CycleError,'agent_context_too_large'):generate(ROOT,self.s,self.plan,'it',ctx,mode='production',call=lambda *a,**k:calls.append(1))
  self.assertEqual(calls,[]);self.assertEqual(self.s.db.execute("select count(*) from agent_reply_decision_v2 where state!='input_blocked'").fetchone()[0],0)
  self.assertEqual(worker.pending_rows(self.s,self.plan,self.now[0]),[])
 def test_restart_and_migration_replay_preserve_exact_handled_scope(self):
  self.add('1','question');q=self.prepare();self.replies.begin(q);self.add('2','later');self.confirm(q)
  self.s.close();self.s=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now[0]);self.replies=AutoReplies(self.s)
  self.assertEqual(apply_database(self.root,'second-cycle')['appliedNow'],[])
  self.assertEqual([r['messageId'] for r in unresolved(self.s,self.plan,'c')],['2'])

 def test_gap_before_dispatch_refuses_even_without_new_pending_revision(self):
  self.add('1','question');q=self.prepare();self.s.db.execute("UPDATE inbox_checkpoint SET state='gap'")
  with self.assertRaisesRegex(CycleError,'reply_context_changed'):self.replies.begin(q)
  self.assertEqual(worker.pending_rows(self.s,self.plan,self.now[0]),[])
 def test_legacy_cursor_backfill_is_versioned_and_does_not_reopen_answered_history(self):
  self.add('1','answered');rowid=self.s.db.execute('select rowid from inbox_event').fetchone()[0]
  self.s.db.execute('INSERT INTO service_cursor VALUES(?,?,?)',(self.plan,'c',rowid))
  self.s.db.execute('DROP TABLE service_message_resolution');self.s.db.execute('DELETE FROM agent_schema_migration WHERE version=25')
  self.assertEqual(len(apply_database(self.root,'second-cycle')['appliedNow']),1)
  evidence=self.s.db.execute('SELECT reference,handled_at FROM service_message_resolution').fetchone()
  self.assertEqual(tuple(evidence),('legacy_service_cursor',None))
  self.add('2','new question');self.assertEqual(self.context()['unansweredMessageIds'],['2'])
  self.assertEqual(apply_database(self.root,'second-cycle')['appliedNow'],[])
 def test_deferred_creator_does_not_hold_queue_and_new_scope_can_run(self):
  self.add('1','question');ctx=self.context()
  for _ in range(3):
   with self.assertRaises(CycleError):generate(ROOT,self.s,self.plan,'it',ctx,mode='production',call=lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
  self.seed('d','456','888');self.add('2','other',creator='d',oec='456',cid='888')
  self.assertEqual([r['creator_id'] for r in worker.pending_rows(self.s,self.plan,self.now[0])],['d'])
  self.add('3','new question')
  self.assertEqual([r['creator_id'] for r in worker.pending_rows(self.s,self.plan,self.now[0])],['c','d'])
