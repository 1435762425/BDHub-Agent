import tempfile,unittest,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402
from lib.workflow_resources import assert_current,claim,heartbeat,recover_expired,release  # noqa:E402

class WorkflowResourceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);(root/'var').mkdir();self.root=root;self.now=[100.0]
  with CycleStore(root/'var/second-cycle.sqlite',lambda:self.now[0]) as store:store.plan('bjn-local-research','it')
  apply_database(root,'second-cycle',clock=lambda:self.now[0]);self.store=CycleStore(root/'var/second-cycle.sqlite',lambda:self.now[0])
  for index in range(4):
   self.store.db.execute("INSERT INTO workflow_stage_run(stage_run_id,run_id,stage,position,state) VALUES(?,?,?,?,?)",(f'stage-{index}',f'run-{index}','kalodata',index,'queued'))
 def tearDown(self):self.store.close();self.tmp.cleanup()

 def test_two_global_slots_and_account_resources_are_atomic(self):
  a=claim(self.store,'stage-0','scheduler-owner-a',[('kalodata:global',2),('supply:acc1',1)],worker_pid=10)
  b=claim(self.store,'stage-1','scheduler-owner-b',[('kalodata:global',2),('supply:acc2',1)],worker_pid=11)
  self.assertNotEqual(a['fence'],b['fence'])
  with self.assertRaisesRegex(CycleError,'resource_busy'):
   claim(self.store,'stage-2','scheduler-owner-c',[('kalodata:global',2),('supply:acc3',1)],worker_pid=12)
  self.assertEqual(self.store.db.execute("SELECT count(*) FROM workflow_resource_slot WHERE resource_key='kalodata:global'").fetchone()[0],2)
  release(self.store,'stage-0','scheduler-owner-a',a['fence'])
  c=claim(self.store,'stage-2','scheduler-owner-c',[('kalodata:global',2),('supply:acc3',1)],worker_pid=12)
  self.assertTrue(assert_current(self.store,'stage-2','scheduler-owner-c',c['fence']))

 def test_heartbeat_fence_and_dead_owner_recovery(self):
  claimed=claim(self.store,'stage-0','scheduler-owner-a',[('communications:acc6',1)],lease_seconds=5,worker_pid=999)
  self.now[0]=103;self.assertEqual(heartbeat(self.store,'stage-0','scheduler-owner-a',claimed['fence'],lease_seconds=5),108)
  with self.assertRaisesRegex(CycleError,'fence_stale'):
   heartbeat(self.store,'stage-0','scheduler-owner-a',claimed['fence']+1,lease_seconds=5)
  self.now[0]=109
  self.assertEqual(recover_expired(self.store,pid_alive=lambda _pid:False),['stage-0'])
  self.assertEqual(self.store.db.execute("SELECT state FROM workflow_stage_run WHERE stage_run_id='stage-0'").fetchone()[0],'queued')
  replacement=claim(self.store,'stage-0','scheduler-owner-b',[('communications:acc6',1)],worker_pid=1000)
  self.assertGreater(replacement['fence'],claimed['fence'])

if __name__=='__main__':unittest.main()
