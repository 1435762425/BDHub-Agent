import json,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.operations_console import console  # noqa:E402
from lib.operations_workflow import create_run,finish_stage  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402
from lib.workflow_dispatch import claim_ready  # noqa:E402

NOW=1_790_000_000.0

class OperationsConsoleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir();(self.root/'config').mkdir()
  for name in ('markets.json','market-accounts.json','operations-policy.json'):
   (self.root/'config'/name).write_bytes((ROOT/'config'/name).read_bytes())
  policy=json.loads((self.root/'config/operations-policy.json').read_text());policy['platformMaxParallelMarkets']=1
  (self.root/'config/operations-policy.json').write_text(json.dumps(policy))
  with CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW) as store:
   for market in ('br','my'):store.plan('bjn-local-research',market)
  apply_database(self.root,'second-cycle',clock=lambda:NOW)
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW)
 def tearDown(self):self.store.close();self.tmp.cleanup()

 def test_an_unreadable_policy_makes_platform_waits_unknown_not_a_guessed_capacity(self):
  create_run(self.store,market='my',trigger_source='manual',scheduled_at=NOW,request_id='console-my-policy',only_stage='catalog',sources=['campaign'])
  create_run(self.store,market='br',trigger_source='manual',scheduled_at=NOW,request_id='console-br-policy',only_stage='oecid',sources=['campaign'])
  from lib.operations_workflow import status
  claim_ready(self.store,self.root,[status(self.store,'my')['current']],{'kalodataMaxParallelMarkets':2,'platformMaxParallelMarkets':1},'scheduler-console-test',worker_pid=1)
  (self.root/'config/operations-policy.json').write_text('{broken')
  rows={row['market']:row for row in console(self.root,self.store,markets=['br','my'])['markets']}
  self.assertEqual((rows['br']['current']['state'],rows['br']['current']['waitingKnown']),('queued',False))

 def test_a_paused_selection_is_shown_only_while_pids_still_wait(self):
  from contextlib import closing
  import sqlite3
  from lib.operations_console import _selection_paused
  from lib.operations_scheduler import SELECTION_BACKLOG_RETRY_SECONDS
  self.store.db.execute("INSERT INTO workflow_generation(generation_id,run_id,stage,market,scope_fingerprint,state,item_count,payload_json,created_at) VALUES('g1','r1','catalog','my','x','published',0,?,?)",
                        (json.dumps({'scope':{'selectionPaused':{'reason':'commerce_verification_required','pending':9}}}),NOW))
  self.assertIsNone(_selection_paused(self.root,self.store.db,'my'))  # no ledger: nothing waits
  with closing(sqlite3.connect(self.root/'var/global-selection-my.sqlite')) as db,db:
   db.executescript("""CREATE TABLE intake_run(id TEXT,rules TEXT,source_run TEXT,created REAL);
     CREATE TABLE intake_item(run_id TEXT,pid TEXT,state TEXT,payload TEXT,updated REAL);
     CREATE TABLE intake_run_member(run_id TEXT,pid TEXT,owner_run_id TEXT);
     INSERT INTO intake_run VALUES('b','{}','s',1);INSERT INTO intake_item VALUES('b','a','pending','{}',1);
     INSERT INTO intake_run_member VALUES('b','a','b');""")
  self.assertEqual(_selection_paused(self.root,self.store.db,'my'),{'reason':'commerce_verification_required','pending':1,
                   'pausedAt':NOW,'retryAt':NOW+SELECTION_BACKLOG_RETRY_SECONDS})

 def test_a_queued_stage_names_the_resource_and_who_holds_it(self):
  from lib.operations_workflow import status
  my=create_run(self.store,market='my',trigger_source='manual',scheduled_at=NOW,request_id='console-my-run',only_stage='catalog',sources=['campaign'])
  create_run(self.store,market='br',trigger_source='manual',scheduled_at=NOW,request_id='console-br-run',only_stage='oecid',sources=['campaign'])
  runs=[status(self.store,m)['current'] for m in ('my','br')]
  claimed=claim_ready(self.store,self.root,[runs[0]],{'kalodataMaxParallelMarkets':2,'platformMaxParallelMarkets':1},'scheduler-console-test',worker_pid=1)
  self.assertEqual(len(claimed['claimed']),1)
  value=console(self.root,self.store,markets=['br','my'])
  rows={row['market']:row for row in value['markets']}
  self.assertEqual((rows['my']['current']['stage'],rows['my']['current']['state']),('catalog','running'))
  br_now=rows['br']['current']
  self.assertEqual((br_now['stage'],br_now['state']),('oecid','queued'))
  self.assertEqual(br_now['waitingOn'][0]['resource'],'platform:global')
  self.assertEqual(br_now['waitingOn'][0]['heldBy'][0]['market'],'my')
  self.assertFalse(rows['br']['lanes']['available'])
  self.assertEqual((value['readOnly'],value['platformWrites']),(True,0))
  self.assertIn('platform:global',{row['resource'] for row in value['resources']})
  # The finished stage becomes the market's last result and a recent run.
  finish_stage(self.store,my['runId'],'catalog',state='completed',item_count=12,complete=True)
  after=console(self.root,self.store,markets=['br','my'])
  mine={row['market']:row for row in after['markets']}['my']
  self.assertEqual((mine['lastFinished']['stage'],mine['lastFinished']['items']),('catalog',12))
  self.assertEqual(after['recent'][0]['market'],'my')

 def test_unknown_resource_needs_read_as_unknown_not_idle(self):
  from unittest import mock
  from lib import workflow_dispatch
  create_run(self.store,market='br',trigger_source='manual',scheduled_at=NOW,request_id='console-br-unknown',only_stage='oecid',sources=['campaign'])
  with mock.patch.object(workflow_dispatch,'resources',side_effect=KeyError('roles')):
   value=console(self.root,self.store,markets=['br'])
  current=value['markets'][0]['current']
  self.assertEqual((current['state'],current['waitingKnown'],current['waitingOn']),('queued',False,[]))
  self.assertIn('roles',current['waitReason'])
 def test_human_queue_uses_the_conversation_classifier_not_open_cases(self):
  plan=self.store.db.execute("SELECT id FROM plan WHERE market='br'").fetchone()[0]
  from lib.cycle_inbox import Inbox;from lib.cycle_service import Service;from lib.cycle_auto_reply import AutoReplies
  Inbox(self.store);Service(self.store);AutoReplies(self.store)
  self.assertIsNotNone(console(self.root,self.store,markets=['br'])['markets'][0]['humanQueue'])
  self.store.db.execute("INSERT INTO relationship VALUES(?,?,?,'human',0,1,0,1)",(plan,'creator-h','909'))
  row={r['market']:r for r in console(self.root,self.store,markets=['br'])['markets']}['br']
  self.assertEqual(row['humanQueue']['human'],1)
  self.assertEqual(row['humanQueue']['human'],1)   # human mode without an open case still needs a person
  self.assertIn(row['openHumanCases'],(0,None))

 def worker_record(self,name,**value):
  (self.root/'var'/name).write_text(json.dumps(value))

 def snapshot(self,market,state,at):
  from lib.market_read_model import publish
  publish(self.store,market,'operations',{'market':market,'stages':[
   {'id':'continuous_send','state':state,'metric':{'value':9,'label':'旧记录'}},
   {'id':'agent_reply','state':state,'metric':{'value':1,'label':'旧记录'}}]},observed_at=at)

 def read_console(self,markets):
  # The actual entry point supplies a read-only store. Any accidental initializer/mutation fails here.
  with CycleStore(self.root/'var/second-cycle.sqlite',clock=lambda:NOW,readonly=True) as reader:
   reader.db.execute('PRAGMA query_only=ON')
   with mock.patch('lib.market_read_model.read',side_effect=AssertionError('projection is not live status')):
    return console(self.root,reader,markets=markets)

 def test_recovered_sender_uses_its_live_sample_instead_of_old_attention(self):
  self.snapshot('br','attention',NOW-35*60)
  self.worker_record('market-send-worker-br.json',pid=100,checkedAt=NOW-5,running=True,state='confirmed')
  before={p.name:p.read_bytes() for p in (self.root/'var').glob('*.json')}
  with mock.patch('lib.process_liveness.record_alive',return_value=True):
   result=self.read_console(['br'])
  lane=result['markets'][0]['lanes']['continuousSend']
  self.assertEqual((lane['state'],lane['fresh'],lane['running']),('confirmed',True,True))
  self.assertEqual(lane['observedAt'],NOW-5)
  self.assertNotEqual(lane['observedAt'],result['checkedAt'])
  self.assertEqual(before,{p.name:p.read_bytes() for p in (self.root/'var').glob('*.json')})
  self.assertEqual((result['readOnly'],result['platformWrites']),(True,0))

 def test_ai_status_remains_live_when_automatic_operations_are_off(self):
  from lib.template_library import DEFAULT_AGENT_SETTING,save_agent_setting
  plan=self.store.plan('bjn-local-research','it')
  save_agent_setting(self.store,plan,0,{key:value for key,value in
   (DEFAULT_AGENT_SETTING|{'enabled':True}).items() if key not in ('revision','updatedAt')})
  self.store.db.execute('UPDATE agent_reply_setting SET updated_at=? WHERE plan_id=?',(NOW-100,plan))
  self.snapshot('it','failed',NOW-27.8*3600)
  self.worker_record('agent-reply-status.json',pid=101,checkedAt=NOW-10,state='outside_reply_window')
  with mock.patch('lib.process_liveness.record_alive',return_value=True):
   row=self.read_console(['it'])['markets'][0]
  self.assertFalse(row['setting']['automaticOperationsEnabled'])
  lane=row['lanes']['agentReply']
  self.assertEqual((lane['state'],lane['enabled'],lane['fresh']),('outside_reply_window',True,True))
  self.assertEqual(lane['observedAt'],NOW-10)
  # No projector is needed for the next independent AI worker observation to appear.
  self.worker_record('agent-reply-status.json',pid=101,checkedAt=NOW-1,state='completed')
  with mock.patch('lib.process_liveness.record_alive',return_value=True):
   lane=self.read_console(['it'])['markets'][0]['lanes']['agentReply']
  self.assertEqual((lane['state'],lane['observedAt']),('completed',NOW-1))

 def test_dead_active_or_stale_workers_cannot_publish_their_last_state_as_current(self):
  from lib.operations_console import LANE_MAX_AGE_SECONDS
  for alive,age,reason in ((True,LANE_MAX_AGE_SECONDS+1,'status_stale'),(False,5,'worker_not_running')):
   with self.subTest(alive=alive,age=age):
    self.worker_record('market-send-worker-br.json',pid=102,checkedAt=NOW-age,running=True,state='sending')
    self.worker_record('agent-reply-status-br.json',pid=103,checkedAt=NOW-age,state='failed')
    with mock.patch('lib.process_liveness.record_alive',return_value=alive):
     lanes=self.read_console(['br'])['markets'][0]['lanes']
    self.assertFalse(lanes['available'])
    for key in ('continuousSend','agentReply'):
     self.assertEqual((lanes[key]['state'],lanes[key]['fresh'],lanes[key]['statusReason']),('unverified',False,reason))
     self.assertEqual(lanes[key]['observedAt'],NOW-age)

 def test_recent_explicit_sender_terminal_states_survive_process_exit(self):
  plan=self.store.db.execute("SELECT id FROM plan WHERE market='br'").fetchone()[0]
  self.store.db.execute("INSERT INTO continuous_send_control VALUES(?,1,1,0,'16:30','24:00','standard',1,?)",(plan,NOW-100))
  for state in ('attention','off','stopped','canary_confirmed'):
   with self.subTest(state=state):
    self.worker_record('market-send-worker-br.json',pid=102,checkedAt=NOW-5,running=False,state=state)
    with mock.patch('lib.process_liveness.record_alive',return_value=False):
     lane=self.read_console(['br'])['markets'][0]['lanes']['continuousSend']
    self.assertEqual((lane['state'],lane['fresh'],lane['running'],lane['statusSource']),(state,True,False,'worker'))
    self.assertEqual(lane['observedAt'],NOW-5)

 def test_an_old_attention_is_unverified_even_though_it_was_terminal(self):
  from lib.operations_console import LANE_MAX_AGE_SECONDS
  self.worker_record('market-send-worker-br.json',pid=102,checkedAt=NOW-LANE_MAX_AGE_SECONDS-1,running=False,state='attention')
  with mock.patch('lib.process_liveness.record_alive',return_value=False):
   lane=self.read_console(['br'])['markets'][0]['lanes']['continuousSend']
  self.assertEqual((lane['state'],lane['fresh'],lane['statusReason']),('unverified',False,'status_stale'))

 def test_current_explicit_off_controls_replace_old_dead_worker_samples(self):
  from lib.continuous_send import publish_runtime
  from lib.template_library import DEFAULT_AGENT_SETTING,save_agent_setting
  it=self.store.plan('bjn-local-research','it')
  br=self.store.db.execute("SELECT id FROM plan WHERE market='br'").fetchone()[0]
  for plan in (it,br):
   self.store.db.execute("INSERT INTO continuous_send_control VALUES(?,0,0,1,'16:30','24:00','standard',1,?)",(plan,NOW-100))
   save_agent_setting(self.store,plan,0,{key:value for key,value in DEFAULT_AGENT_SETTING.items() if key not in ('revision','updatedAt')})
  self.store.clock=lambda:NOW-86400
  publish_runtime(self.store,it,'failed',worker_pid=108)
  self.store.clock=lambda:NOW
  self.worker_record('continuous-send-worker.json',pid=108,startedAt=NOW-2*86400)
  self.worker_record('market-send-worker-br.json',pid=109,checkedAt=NOW-86400,running=False,state='attention')
  self.worker_record('agent-reply-status.json',pid=110,checkedAt=NOW-86400,state='failed')
  self.worker_record('agent-reply-status-br.json',pid=111,checkedAt=NOW-86400,state='failed')
  with mock.patch('lib.process_liveness.record_alive',return_value=False):
   rows=self.read_console(['it','br'])['markets']
  for row in rows:
   for key,state in (('continuousSend','off'),('agentReply','disabled')):
    lane=row['lanes'][key]
    self.assertEqual((lane['state'],lane['fresh'],lane['enabled'],lane['statusSource']),(state,True,False,'control'))
    self.assertEqual(lane['observedAt'],NOW)
    self.assertEqual(lane['workerObservedAt'],NOW-86400)

 def test_control_close_does_not_replace_a_recent_canary_stop_receipt(self):
  plan=self.store.db.execute("SELECT id FROM plan WHERE market='br'").fetchone()[0]
  self.store.db.execute("INSERT INTO continuous_send_control VALUES(?,0,0,1,'16:30','24:00','standard',1,?)",(plan,NOW-10))
  self.worker_record('market-send-worker-br.json',pid=112,checkedAt=NOW-5,running=False,state='canary_confirmed')
  with mock.patch('lib.process_liveness.record_alive',return_value=False):
   lane=self.read_console(['br'])['markets'][0]['lanes']['continuousSend']
  self.assertEqual((lane['state'],lane['fresh'],lane['statusSource']),('canary_confirmed',True,'worker'))

 def test_a_new_launcher_or_control_change_invalidates_the_old_terminal(self):
  from lib.operations_console import _lane
  with mock.patch('lib.process_liveness.record_alive',return_value=False):
   lane=_lane({'pid':114,'startedAt':NOW-1},NOW,sample={'state':'attention','checkedAt':NOW-5},enabled=True,control_at=NOW-100)
   self.assertEqual((lane['state'],lane['statusReason']),('unverified','previous_worker_sample'))
   lane=_lane({'pid':114,'startedAt':NOW-100},NOW,sample={'pid':113,'state':'attention','checkedAt':NOW-5},enabled=True,control_at=NOW-100)
   self.assertEqual((lane['state'],lane['statusReason']),('unverified','previous_worker_sample'))
   lane=_lane({'pid':114,'state':'attention','checkedAt':NOW-5},NOW,enabled=True,control_at=NOW-1)
   self.assertEqual((lane['state'],lane['statusReason']),('unverified','control_changed'))

 def test_missing_controls_cannot_manufacture_a_current_disabled_state(self):
  self.worker_record('market-send-worker-br.json',pid=115,checkedAt=NOW-86400,running=False,state='off')
  self.worker_record('agent-reply-status-br.json',pid=116,checkedAt=NOW-86400,state='disabled')
  with mock.patch('lib.process_liveness.record_alive',return_value=False):
   lanes=self.read_console(['br'])['markets'][0]['lanes']
  for key in ('continuousSend','agentReply'):
   self.assertIsNone(lanes[key]['enabled'])
   self.assertEqual((lanes[key]['state'],lanes[key]['fresh']),('unverified',False))
  self.worker_record('market-send-worker-br.json',pid=115,checkedAt=NOW-1,running=False,state='attention')
  with mock.patch('lib.process_liveness.record_alive',return_value=False):
   lane=self.read_console(['br'])['markets'][0]['lanes']['continuousSend']
  self.assertEqual((lane['state'],lane['statusReason']),('unverified','control_unavailable'))

 def test_missing_or_malformed_status_does_not_borrow_another_market_or_projection(self):
  self.snapshot('br','attention',NOW-5)
  self.worker_record('market-send-worker-my.json',pid=104,checkedAt=NOW-5,running=True,state='confirmed')
  (self.root/'var/agent-reply-status-br.json').write_text('{broken')
  with mock.patch('lib.process_liveness.record_alive',return_value=True):
   rows={row['market']:row for row in self.read_console(['br','my'])['markets']}
  for lane in ('continuousSend','agentReply'):
   self.assertEqual(rows['br']['lanes'][lane]['state'],'unverified')
   self.assertIsNone(rows['br']['lanes'][lane]['observedAt'])
  self.assertEqual(rows['my']['lanes']['continuousSend']['state'],'confirmed')

 def test_it_sender_uses_ledger_sample_and_checks_launcher_identity(self):
  from lib.continuous_send import publish_runtime
  plan=self.store.plan('bjn-local-research','it')
  self.store.clock=lambda:NOW-5
  publish_runtime(self.store,plan,'sending',worker_pid=105)
  self.store.clock=lambda:NOW
  self.worker_record('continuous-send-worker.json',pid=105,startedAt=NOW-100)
  with mock.patch('lib.process_liveness.record_alive',return_value=True):
   lane=self.read_console(['it'])['markets'][0]['lanes']['continuousSend']
  self.assertEqual((lane['state'],lane['observedAt'],lane['fresh']),('sending',NOW-5,True))
  # A new launcher must not lend its live PID to the preceding worker's stale state.
  self.worker_record('continuous-send-worker.json',pid=106,startedAt=NOW-1)
  with mock.patch('lib.process_liveness.record_alive',return_value=True):
   lane=self.read_console(['it'])['markets'][0]['lanes']['continuousSend']
  self.assertEqual((lane['state'],lane['statusReason']),('unverified','previous_worker_sample'))

 def test_a_new_control_revision_waits_for_a_new_worker_sample(self):
  plan=self.store.db.execute("SELECT id FROM plan WHERE market='br'").fetchone()[0]
  self.store.db.execute("INSERT INTO continuous_send_control VALUES(?,1,0,0,'16:30','24:00','standard',1,?)",(plan,NOW))
  self.worker_record('market-send-worker-br.json',pid=107,checkedAt=NOW-5,running=True,state='attention')
  with mock.patch('lib.process_liveness.record_alive',return_value=True):
   lane=self.read_console(['br'])['markets'][0]['lanes']['continuousSend']
  self.assertEqual((lane['state'],lane['statusReason'],lane['enabled']),('unverified','control_changed',True))
  self.worker_record('market-send-worker-br.json',pid=107,checkedAt=NOW,running=True,state='waiting_window')
  with mock.patch('lib.process_liveness.record_alive',return_value=True):
   lane=self.read_console(['br'])['markets'][0]['lanes']['continuousSend']
  self.assertEqual((lane['state'],lane['fresh']),('waiting_window',True))

if __name__=='__main__':unittest.main()
