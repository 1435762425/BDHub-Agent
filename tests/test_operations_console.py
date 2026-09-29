import json,sys,tempfile,unittest
from pathlib import Path
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

if __name__=='__main__':unittest.main()
