import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib import workflow_yield as Y  # noqa:E402
from lib.operations_workflow import create_run  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402
from lib.workflow_resources import claim  # noqa:E402

NOW=1_790_000_000.0

class YieldTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir()
  with CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW) as store:
   for m in ('it','my'):store.plan('bjn-local-research',m)
  apply_database(self.root,'second-cycle',clock=lambda:NOW)
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW)
  run=create_run(self.store,market='my',trigger_source='manual',scheduled_at=NOW,request_id='yield-my-catalog',only_stage='catalog',sources=['campaign'])
  self.stage=next(r for r in run['stages'] if r['state']=='queued')
  self.ticket=claim(self.store,self.stage['stageRunId'],'scheduler-yield-owner',[('workflow:my',1),('supply:acc5',1),('platform:global',1)],worker_pid=1)
  self.env={Y.ENV:json.dumps({'stageRunId':self.stage['stageRunId'],'ownerId':'scheduler-yield-owner','fence':self.ticket['fence']})}
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def holder(self):
  row=self.store.db.execute("SELECT owner_stage_run_id FROM workflow_resource_slot WHERE resource_key='platform:global'").fetchone()
  return row[0] if row else None
 def queue_other(self):
  run=create_run(self.store,market='it',trigger_source='manual',scheduled_at=NOW,request_id='yield-it-oecid',only_stage='oecid',sources=['campaign'])
  return next(r for r in run['stages'] if r['state']=='queued')

 def test_nothing_happens_without_a_claim_or_without_anyone_waiting(self):
  with mock.patch.dict(os.environ,{},clear=False):
   os.environ.pop(Y.ENV,None)
   self.assertEqual(Y.yield_platform(self.root,sleep=lambda _s:None),0)
  with mock.patch.dict(os.environ,self.env):
   self.assertEqual(Y.yield_platform(self.root,sleep=lambda _s:None),0)
  self.assertEqual(self.holder(),self.stage['stageRunId'])

 def test_a_waiting_market_gets_the_slot_and_the_stage_takes_it_back_afterwards(self):
  other=self.queue_other();events=[]
  def sleep(seconds):
   # During the hand-over the other market's stage takes the slot, runs, and releases it.
   if not events:
    events.append(self.holder())
    t=claim(self.store,other['stageRunId'],'scheduler-other-owner',[('workflow:it',1),('platform:global',1)],worker_pid=2)
    events.append(self.holder())
    from lib.workflow_resources import release
    release(self.store,other['stageRunId'],'scheduler-other-owner',t['fence'])
  with mock.patch.dict(os.environ,self.env):
   Y.yield_platform(self.root,sleep=sleep)
  self.assertEqual(events,[None,other['stageRunId']])
  self.assertEqual(self.holder(),self.stage['stageRunId'])
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM workflow_resource_slot WHERE owner_stage_run_id=?',(self.stage['stageRunId'],)).fetchone()[0],3)

 def test_a_slot_that_never_comes_back_stops_the_child(self):
  self.queue_other();clock=[0.0]
  def sleep(seconds):
   clock[0]+=seconds
   if not self.holder():self.store.db.execute("INSERT INTO workflow_resource_slot VALUES('platform:global',0,'someone-else',999,?,?)",(NOW+999,NOW))
  with mock.patch.dict(os.environ,self.env),self.assertRaisesRegex(CycleError,'platform_slot_reclaim_timeout'):
   Y.yield_platform(self.root,sleep=sleep,monotonic=lambda:clock[0])

if __name__=='__main__':unittest.main()
