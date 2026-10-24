import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('agent_reply_worker',ROOT/'scripts/run-agent-replies.py')
WORKER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(WORKER)
BEIJING=timezone(timedelta(hours=8))

class Store:
 def __init__(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
 def close(self):self.db.close()

class AgentReplyWorkerTests(unittest.TestCase):
 def setUp(self):self.store=Store()
 def tearDown(self):self.store.close()
 def test_reply_window_is_short_and_end_exclusive(self):
  setting={'replyStart':'15:00','replyEnd':'16:00'}
  stamp=lambda h,m:datetime(2026,9,20,h,m,tzinfo=BEIJING).timestamp()
  self.assertFalse(WORKER.inside(setting,stamp(14,59)))
  self.assertTrue(WORKER.inside(setting,stamp(15,0)))
  self.assertFalse(WORKER.inside(setting,stamp(16,0)))
 def test_scheduled_replies_keep_clear_of_the_send_window_on_both_sides(self):
  stamp=lambda h,m:datetime(2026,9,20,h,m,tzinfo=BEIJING).timestamp()
  late=['16:30','24:00']
  self.assertEqual([WORKER.near_send_window(late,30,stamp(h,m)) for h,m in ((15,59),(16,0),(23,59),(0,15),(0,30))],
                   [False,True,True,True,False])
  day=['10:00','12:00']
  self.assertEqual([WORKER.near_send_window(day,30,stamp(h,m)) for h,m in ((9,29),(9,30),(12,29),(12,30))],
                   [False,True,True,False])
 def test_one_time_authorization_requires_a_bounded_request_id(self):
  self.assertEqual(WORKER.authorized_request('agent-now-0001'),'agent-now-0001')
  with self.assertRaisesRegex(Exception,'agent_reply_authorization_invalid'):
   WORKER.authorized_request('../unsafe')
 def test_due_pending_survives_the_old_short_freeze_deadline(self):
  db=self.store.db
  db.execute('CREATE TABLE inbox_pending(plan_id TEXT,creator_id TEXT,state TEXT,due_at REAL)')
  db.execute('CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,mode TEXT,rejected INTEGER,inbox_until REAL)')
  db.execute('CREATE TABLE service_case(plan_id TEXT,creator_id TEXT,state TEXT)')
  db.execute("INSERT INTO inbox_pending VALUES('p','c','policy_review',10)")
  db.execute("INSERT INTO relationship VALUES('p','c','auto',0,11)")
  rows=WORKER.pending_rows(self.store,'p',1000)
  self.assertEqual([row['creator_id'] for row in rows],['c'])
 def test_waiting_send_batch_does_not_block_reply_window_but_active_dispatch_does(self):
  db=self.store.db
  db.execute('CREATE TABLE cycle_delivery_part(state TEXT)')
  db.execute('CREATE TABLE cycle_bulk_freeze(batch_id TEXT,state TEXT,created_at REAL)')
  db.execute('CREATE TABLE cycle_bulk_runtime(batch_id TEXT,phase TEXT)')
  db.execute("INSERT INTO cycle_bulk_freeze VALUES('batch','running',1)")
  db.execute("INSERT INTO cycle_bulk_runtime VALUES('batch','waiting_window')")
  self.assertFalse(WORKER.send_dispatch_active(self.store))
  db.execute("UPDATE cycle_bulk_runtime SET phase='cohort'")
  self.assertTrue(WORKER.send_dispatch_active(self.store))
  db.execute("UPDATE cycle_bulk_runtime SET phase='waiting_window'")
  db.execute("INSERT INTO cycle_delivery_part VALUES('inflight')")
  self.assertTrue(WORKER.send_dispatch_active(self.store))
 def test_unknown_delivery_or_reply_holds_the_agent_before_any_model_call(self):
  from lib.cycle_auto_reply import reply_blocker
  db=self.store.db
  db.execute('CREATE TABLE cycle_delivery(plan_id TEXT,state TEXT)')
  db.execute('CREATE TABLE service_reply(plan_id TEXT,state TEXT)')
  db.execute("INSERT INTO cycle_delivery VALUES('p','quarantined_unknown')")
  db.execute("INSERT INTO service_reply VALUES('p','confirmed')")
  db.execute("INSERT INTO cycle_delivery VALUES('other','unknown')")
  self.assertIsNone(reply_blocker(db,'p'))
  db.execute("INSERT INTO service_reply VALUES('p','inflight')")
  self.assertEqual(reply_blocker(db,'p'),'reply_unknown')
  db.execute("INSERT INTO cycle_delivery VALUES('p','unknown')")
  self.assertEqual(reply_blocker(db,'p'),'delivery_unknown')
 def test_dispatch_mutex_is_limited_to_the_same_market(self):
  db=self.store.db
  db.execute('CREATE TABLE plan(id TEXT,market TEXT)')
  db.execute('CREATE TABLE cycle_delivery(id TEXT,plan_id TEXT)')
  db.execute('CREATE TABLE cycle_delivery_part(delivery_id TEXT,state TEXT)')
  db.execute("INSERT INTO plan VALUES('br-plan','br'),('it-plan','it')")
  db.execute("INSERT INTO cycle_delivery VALUES('br-delivery','br-plan')")
  db.execute("INSERT INTO cycle_delivery_part VALUES('br-delivery','inflight')")
  self.assertTrue(WORKER.send_dispatch_active(self.store,'br'))
  self.assertFalse(WORKER.send_dispatch_active(self.store,'my'))
  db.execute("UPDATE cycle_delivery_part SET state='confirmed'")
  self.assertFalse(WORKER.send_dispatch_active(self.store,'br'))
 def test_failed_model_turn_is_deferred_without_blocking_a_new_turn(self):
  db=self.store.db
  db.execute('CREATE TABLE agent_reply_decision_v2(plan_id TEXT,turn_id TEXT,mode TEXT,state TEXT,created_at REAL)')
  db.execute("INSERT INTO agent_reply_decision_v2 VALUES('p','old','production','unknown',100)")
  self.assertFalse(WORKER.decision_retry_ready(self.store,'p','old',200))
  self.assertTrue(WORKER.decision_retry_ready(self.store,'p','old',3700))
  self.assertTrue(WORKER.decision_retry_ready(self.store,'p','new',200))
  db.execute("INSERT INTO agent_reply_decision_v2 VALUES('p','old','production','unknown',200)")
  db.execute("INSERT INTO agent_reply_decision_v2 VALUES('p','old','production','unknown',300)")
  self.assertFalse(WORKER.decision_retry_ready(self.store,'p','old',10000))
 def test_waiting_for_creator_is_not_reclassified_without_new_input(self):
  db=self.store.db
  db.execute('CREATE TABLE inbox_pending(plan_id TEXT,creator_id TEXT,state TEXT,due_at REAL)')
  db.execute('CREATE TABLE relationship(plan_id TEXT,creator_id TEXT,mode TEXT,rejected INTEGER)')
  db.execute('CREATE TABLE service_case(plan_id TEXT,creator_id TEXT,state TEXT)')
  db.execute("INSERT INTO relationship VALUES('p','c','auto',0)")
  db.execute("INSERT INTO inbox_pending VALUES('p','c','waiting_contact',10)")
  self.assertEqual(WORKER.pending_rows(self.store,'p',1000),[])
  db.execute("UPDATE inbox_pending SET state='awaiting_content'")
  self.assertEqual(len(WORKER.pending_rows(self.store,'p',1000)),1)

 def test_reply_queue_selects_one_creator_before_generating(self):
  db=self.store.db
  db.execute('CREATE TABLE inbox_pending(plan_id,creator_id,state,due_at)')
  db.execute('CREATE TABLE relationship(plan_id,creator_id,mode,rejected)')
  db.execute('CREATE TABLE service_case(plan_id,creator_id,state)')
  for creator,stamp in [('older',10),('newer',20)]:
   db.execute("INSERT INTO relationship VALUES('p',?,'auto',0)",(creator,))
   db.execute("INSERT INTO inbox_pending VALUES('p',?,'awaiting_content',?)",(creator,stamp))
  selected=WORKER.pending_rows(self.store,'p',100,limit=1)
  self.assertEqual([r['creator_id'] for r in selected],['older'])
  self.assertEqual(len(WORKER.pending_rows(self.store,'p',100)),2)

 def test_useful_reply_progress_has_no_fixed_minute_idle_but_errors_back_off(self):
  for result in ({'state':'completed','claimed':1},{'state':'completed','confirmed':1},
                 {'state':'completed','noReply':1},{'state':'original_intent_rechecked','replyState':'confirmed'}):
   self.assertEqual(WORKER.worker_delay(result,60),1)
  for result in ({'state':'failed'},{'state':'outside_reply_window'},
                 {'state':'completed','deferred':1},{'state':'original_intent_rechecked','replyState':'unknown'}):
   self.assertEqual(WORKER.worker_delay(result,60),60)
  self.assertEqual(WORKER.worker_delay({'state':'waiting_dispatch'},1),30)

class AgentReplyAuthorizationTests(unittest.TestCase):
 def setUp(self):
  from lib.schema_migrations import apply_database
  from lib.second_cycle import CycleStore
  from lib.cycle_inbox import Inbox
  from lib.cycle_auto_reply import AutoReplies
  from lib.cycle_delivery import Deliveries
  from lib.template_library import DEFAULT_AGENT_SETTING,save_agent_setting
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1800000000
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:self.plan=store.plan('bjn-local-research','it')
  apply_database(self.root,'second-cycle')
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now)
  Inbox(self.store);Deliveries(self.store);self.replies=AutoReplies(self.store)
  (self.root/'config').mkdir()
  (self.root/'config/market-accounts.json').write_text((ROOT/'config/market-accounts.json').read_text())
  from lib.account_identity import publish_generation
  publish_generation(self.store,market='it',account='acc6',role='communications',reason='baseline',
   identity={'browserRef':'browser','httpRef':'http','imRef':'im','institutionFingerprint':'institution'},
   capabilities={'agent_reply':{'state':'not_tested'}})
  setting={k:v for k,v in DEFAULT_AGENT_SETTING.items() if k not in ('revision','updatedAt')}
  save_agent_setting(self.store,self.plan,0,{**setting,'enabled':True})
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def ready(self,n):
  from lib.second_cycle import encoded
  creator='creator-'+str(n);cid=str(100+n);oec=str(200+n);turn='turn-'+str(n)
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,?,1)",(self.plan,creator,oec,self.now+3600))
  self.store.db.execute("INSERT INTO inbox_pending VALUES(?,?,1,?,'awaiting_content')",(self.plan,creator,self.now-1))
  self.store.db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',
   (self.plan,cid,'1',oec,'creatorReplies',self.now*1000,encoded({'kind':'creatorReplies'}),0,self.now))
  self.store.db.execute('INSERT INTO inbound_turn VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
   (turn,self.plan,creator,oec,cid,'1','a'*64,'text','Ciao',self.now*1000,0,self.now))
  self.replies.service.capture(self.plan,cid,oec,[{'messageId':'1','format':'text','text':'Ciao','nativeType':'text','rawSha256':'a'*64}])
  return self.replies.prepare_generated(self.plan,creator,cid,1,turn,'agent-decision-'+str(n),'Grazie!',expected_control_revision=1)
 def pilot(self):
  from lib.agent_reply_v2 import authorize_rollout
  return authorize_rollout(self.store,self.plan,'it','pilot','page-pilot-authorization')
 def confirm(self,q):
  self.replies.confirm(q['id'],{'status':'confirmed','requestRef':q['request_ref'],
   'conversationId':q['cid'],'messageId':'90','evidenceRef':'exact-proof'})
 def failed_tick(self,error,expected):
  draft=self.ready(1);self.pilot()
  self.store.db.execute("INSERT INTO agent_reply_run VALUES('historical-failed',?,'failed',?,?,0,0,0,0,0,0,NULL)",
                       (self.plan,self.now-10,self.now-9))
  # Failure status, finish time and safe reason must be visible in the same update.
  self.store.db.execute("""CREATE TRIGGER complete_failure_reason BEFORE UPDATE OF state ON agent_reply_run
   WHEN NEW.state='failed' AND (NEW.error IS NULL OR NEW.finished_at IS NULL)
   BEGIN SELECT RAISE(ABORT,'incomplete_failure_record'); END""")
  @contextmanager
  def opened(*_a,**_kw):yield self.store
  with patch.object(WORKER,'CycleStore',opened),patch.object(WORKER,'ROOT',self.root), \
       patch.object(WORKER,'backfill',return_value={}), \
       patch.object(WORKER,'generate',side_effect=AssertionError('existing_draft_must_not_regenerate')), \
       patch.object(WORKER,'run_existing',side_effect=error):
   with self.assertRaises(type(error)) as caught:WORKER.tick('page-error-test-0001','it')
  self.assertIs(caught.exception,error)
  row=self.store.db.execute("SELECT * FROM agent_reply_run WHERE run_id<>'historical-failed'").fetchone()
  self.assertEqual((row['state'],row['finished_at'],row['error']),('failed',self.now,expected))
  self.assertIsNone(self.store.db.execute("SELECT error FROM agent_reply_run WHERE run_id='historical-failed'").fetchone()[0])
  self.assertEqual(self.replies.get(draft['id']),draft)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM service_reply').fetchone()[0],1)
  return row['error']
 def test_typed_failure_records_safe_code_atomically_without_changing_original_draft(self):
  from lib.italy_im_session import ItalyImReadError
  error=ItalyImReadError('im_transport_error');error.args=('Authorization: Bearer secret-token; prompt=private-content',)
  self.assertEqual(self.failed_tick(error,'im_transport_error'),'im_transport_error')
 def test_untyped_failure_records_only_its_class_without_reading_sensitive_message(self):
  class SensitiveFailure(RuntimeError):
   code='Authorization: Bearer secret-token; prompt=private-content'
   def __str__(self):raise AssertionError('raw_exception_message_must_not_be_read')
  self.assertEqual(self.failed_tick(SensitiveFailure(),'SensitiveFailure'),'SensitiveFailure')
 def test_no_page_grant_cannot_begin_a_v2_reply(self):
  from lib.second_cycle import CycleError
  q=self.ready(1)
  with self.assertRaisesRegex(CycleError,'agent_rollout_authorization_required'):self.replies.begin(q['id'])
  self.assertEqual(self.replies.get(q['id'])['state'],'ready')
 def test_first_pilot_is_allowed_and_confirmation_blocks_the_next_begin(self):
  from lib.second_cycle import CycleError
  first=self.ready(1);second=self.ready(2);self.pilot()
  self.assertTrue(self.replies.begin(first['id'])['dispatchAllowed']);self.confirm(first)
  with self.assertRaisesRegex(CycleError,'agent_rollout_authorization_required'):self.replies.begin(second['id'])
  self.assertEqual(self.replies.get(second['id'])['state'],'ready')
 def test_full_page_authorization_allows_the_next_reply(self):
  from lib.agent_reply_v2 import authorize_rollout
  first=self.ready(1);second=self.ready(2);self.pilot();self.replies.begin(first['id']);self.confirm(first)
  authorize_rollout(self.store,self.plan,'it','full','page-full-authorization')
  self.assertTrue(self.replies.begin(second['id'])['dispatchAllowed'])
 def test_isolated_first_pilot_keeps_its_single_submission_grant(self):
  from lib.second_cycle import CycleError
  first=self.ready(1);second=self.ready(2);self.pilot();self.replies.begin(first['id'])
  self.replies.unknown(first['id']);self.replies.isolate(first['id'])
  with self.assertRaisesRegex(CycleError,'agent_pilot_already_submitted'):self.replies.begin(second['id'])
  self.assertEqual(self.replies.get(second['id'])['state'],'ready')
 def test_worker_waits_for_isolated_pilot_without_model_or_reply_transport(self):
  first=self.ready(1);second=self.ready(2);self.pilot();self.replies.begin(first['id'])
  self.replies.unknown(first['id']);self.replies.isolate(first['id'])
  @contextmanager
  def opened(*_a,**_kw):yield self.store
  with patch.object(WORKER,'CycleStore',opened),patch.object(WORKER,'ROOT',self.root), \
       patch.object(WORKER,'production_context',side_effect=AssertionError('context_after_pilot_submission')), \
       patch.object(WORKER,'generate',side_effect=AssertionError('model_after_pilot_submission')), \
       patch.object(WORKER,'run_existing',side_effect=AssertionError('transport_after_pilot_submission')):
   result=WORKER.tick('page-authorized-now','it')
  self.assertEqual(result,{'state':'pilot_waiting_confirmation','platformWrites':0,'realSends':0})
  self.assertEqual(self.replies.get(second['id'])['state'],'ready')
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM agent_reply_run').fetchone()[0],0)
 def passive_confirmation_tick(self,isolated):
  from lib.second_cycle import encoded
  first=self.ready(1);second=self.ready(2);self.pilot()
  self.replies.begin(first['id'],{'account':'acc6','identity':'b'*64})
  self.replies.accepted(first['id'],{'requestRef':first['request_ref'],'messageId':'90'});self.replies.unknown(first['id'])
  if isolated:self.replies.isolate(first['id'])
  self.now+=1
  self.store.db.execute('INSERT INTO inbox_event VALUES(?,?,?,?,?,?,?,?,?)',
   (self.plan,first['cid'],'90',first['oec'],'ourMessages',self.now*1000,encoded({'kind':'ourMessages'}),0,self.now))
  self.replies.service.capture(self.plan,first['cid'],first['oec'],[{'messageId':'90','format':'text',
   'text':first['text'],'nativeType':'text','rawSha256':'b'*64}])
  @contextmanager
  def opened(*_a,**_kw):yield self.store
  with patch.object(WORKER,'CycleStore',opened),patch.object(WORKER,'ROOT',self.root), \
       patch.object(WORKER,'generate',side_effect=AssertionError('model_after_pilot_confirmation')), \
       patch.object(WORKER,'run_existing',side_effect=AssertionError('second_send_after_pilot_confirmation')):
   result=WORKER.tick('page-authorized-now','it')
  self.assertEqual(result,{'state':'pilot_complete_waiting_resume','platformWrites':0,'realSends':0})
  self.assertEqual(self.replies.get(first['id'])['state'],'confirmed')
  self.assertEqual(self.replies.get(second['id'])['state'],'ready')
  from lib.account_identity import current_generation
  self.assertEqual(current_generation(self.store,'it','acc6')['capabilities']['agent_reply']['state'],'verified')
  self.assertIsNone(self.store.db.execute("SELECT 1 FROM control_event WHERE event_id='agent-v2-full-run'").fetchone())
 def test_unknown_passive_confirmation_recomputes_stage_before_sending(self):self.passive_confirmation_tick(False)
 def test_isolated_passive_confirmation_recomputes_stage_before_sending(self):self.passive_confirmation_tick(True)
 def test_restart_completes_already_confirmed_pilot_before_waiting_for_resume(self):
  from lib.account_identity import current_generation
  first=self.ready(1);self.pilot();self.replies.begin(first['id'],{'account':'acc6','identity':'b'*64});self.confirm(first)
  self.assertEqual(current_generation(self.store,'it','acc6')['capabilities']['agent_reply']['state'],'not_tested')
  @contextmanager
  def opened(*_a,**_kw):yield self.store
  with patch.object(WORKER,'CycleStore',opened),patch.object(WORKER,'ROOT',self.root), \
       patch.object(WORKER,'generate',side_effect=AssertionError('model_after_confirmed_pilot')), \
       patch.object(WORKER,'run_existing',side_effect=AssertionError('transport_after_confirmed_pilot')):
   result=WORKER.tick('page-authorized-now','it')
  self.assertEqual(result,{'state':'pilot_complete_waiting_resume','platformWrites':0,'realSends':0})
  self.assertEqual(current_generation(self.store,'it','acc6')['capabilities']['agent_reply']['state'],'verified')


if __name__=='__main__':unittest.main()
