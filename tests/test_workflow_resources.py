import tempfile,unittest,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleError,CycleStore  # noqa:E402
from lib.workflow_resources import assert_current,assert_owned,claim,heartbeat,record_late_result,recover_expired,release  # noqa:E402

class WorkflowResourceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);(root/'var').mkdir();self.root=root;self.now=[100.0]
  with CycleStore(root/'var/second-cycle.sqlite',lambda:self.now[0]) as store:store.plan('bjn-local-research','it')
  apply_database(root,'second-cycle',clock=lambda:self.now[0]);self.store=CycleStore(root/'var/second-cycle.sqlite',lambda:self.now[0])
  for index in range(4):
   self.store.db.execute("INSERT INTO workflow_run(run_id,market,trigger_source,scheduled_at,applicable_sources_json,config_revision,state,started_at) VALUES(?,?,?,?,?,?,?,?)",(f'run-{index}','it','manual',100,'[]',0,'queued',100))
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

 def test_expired_live_owner_keeps_resource_until_explicit_recovery(self):
  import os
  claim(self.store,'stage-0','scheduler-owner-a',[('supply:acc9',1)],lease_seconds=5,worker_pid=os.getpid(),input_generation_id='generation-prior')
  self.assertEqual(self.store.db.execute("SELECT input_generation_id FROM workflow_stage_run WHERE stage_run_id='stage-0'").fetchone()[0],'generation-prior')
  self.now[0]=106
  with self.assertRaisesRegex(CycleError,'resource_busy'):
   claim(self.store,'stage-1','scheduler-owner-b',[('supply:acc9',1)],worker_pid=99999)
  self.assertEqual(recover_expired(self.store,pid_alive=lambda pid:pid==os.getpid()),[])
  with self.assertRaisesRegex(CycleError,'resource_busy'):
   claim(self.store,'stage-1','scheduler-owner-b',[('supply:acc9',1)],worker_pid=99999)
  self.assertEqual(recover_expired(self.store,pid_alive=lambda _pid:False),['stage-0'])
  claim(self.store,'stage-1','scheduler-owner-b',[('supply:acc9',1)],worker_pid=99999)


 def test_a_lapsed_but_unrecovered_fence_still_settles_and_a_recovered_one_keeps_evidence(self):
  claimed=claim(self.store,'stage-0','scheduler-owner-a',[('platform:global',1)],lease_seconds=5,worker_pid=999)
  self.now[0]=200
  with self.assertRaisesRegex(CycleError,'fence_stale'):assert_current(self.store,'stage-0','scheduler-owner-a',claimed['fence'])
  self.assertTrue(assert_owned(self.store,'stage-0','scheduler-owner-a',claimed['fence']))
  recover_expired(self.store,pid_alive=lambda _pid:False)
  with self.assertRaisesRegex(CycleError,'fence_stale'):assert_owned(self.store,'stage-0','scheduler-owner-a',claimed['fence'])
  record_late_result(self.store,'stage-0','scheduler-owner-a',claimed['fence'],{'state':'completed','platformWrites':3})
  record_late_result(self.store,'stage-0','scheduler-owner-a',claimed['fence'],{'state':'completed','platformWrites':3})
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM workflow_late_result').fetchone()[0],1)

 def test_an_expired_write_capable_stage_is_never_requeued_on_its_own(self):
  self.store.db.execute("UPDATE workflow_stage_run SET stage='taplink_prepare' WHERE stage_run_id='stage-1'")
  claim(self.store,'stage-1','scheduler-owner-a',[('platform:global',1)],lease_seconds=5,worker_pid=999)
  claim(self.store,'stage-0','scheduler-owner-b',[('kalodata:global',2)],lease_seconds=5,worker_pid=998)
  self.now[0]=200
  self.assertEqual(sorted(recover_expired(self.store,pid_alive=lambda _pid:False)),['stage-0','stage-1'])
  rows=dict(self.store.db.execute("SELECT stage_run_id,state||':'||error_code FROM workflow_stage_run WHERE stage_run_id IN ('stage-0','stage-1')"))
  self.assertEqual(rows,{'stage-0':'queued:recovery_claim_expired','stage-1':'needs_human:recovery_claim_expired_write_unknown'})
  self.assertEqual(self.store.db.execute("SELECT state FROM workflow_run WHERE run_id='run-1'").fetchone()[0],'needs_human')
  self.assertIn('"writeEvidence":"uncertain"',self.store.db.execute("SELECT counts_json FROM workflow_stage_run WHERE stage_run_id='stage-1'").fetchone()[0])
  # Nothing is left holding the platform slot.
  claim(self.store,'stage-2','scheduler-owner-c',[('platform:global',1)],worker_pid=997)

if __name__=='__main__':unittest.main()
