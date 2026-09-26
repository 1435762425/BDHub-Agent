import tempfile
import unittest
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402
from lib.workflow_dispatch import claim_ready,resources  # noqa:E402
from lib.workflow_resources import release  # noqa:E402

MARKETS=('be','br','de','it','jp','my','mx','nl','ph','sg','th','uk','us','vn')


class DispatchTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'var').mkdir();self.now=[100.0]
  with CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now[0]) as store:store.plan('bjn-local-research','it')
  apply_database(self.root,'second-cycle',clock=lambda:self.now[0]);self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now[0])
  self.accounts={market:{'roles':{'supply':f'acc{i+1}','communications':f'acc{i+21}'}} for i,market in enumerate(MARKETS)}
  self.policy={'kalodataMaxParallelMarkets':2}
 def tearDown(self):self.store.close();self.temp.cleanup()

 def seed(self,stage='catalog',*,checkpoint_market=None,blocked_market=None):
  runs=[]
  for index,market in enumerate(MARKETS):
   run_id=f'run-{market}';stage_id=f'stage-{market}';state='needs_human' if market==blocked_market else 'queued'
   self.store.db.execute("INSERT INTO workflow_run(run_id,market,trigger_source,scheduled_at,applicable_sources_json,config_revision,state,started_at) VALUES(?,?,?,?,?,?,?,?)",
                         (run_id,market,'schedule',100,'[]',0,state,100))
   self.store.db.execute('INSERT INTO workflow_stage_run(stage_run_id,run_id,stage,position,state) VALUES(?,?,?,?,?)',
                         (stage_id,run_id,stage,0,'queued'))
   runs.append({'runId':run_id,'market':market,'state':state,'stages':[{'stageRunId':stage_id,'stage':stage,'position':0,
                 'state':'queued','checkpoint':{'page':3} if market==checkpoint_market else {},'outputGenerationId':None}]})
  return runs

 def test_platform_stages_run_one_market_at_a_time_even_on_separate_accounts(self):
  for stage in ('catalog','taplink_clean','taplink_prepare','oecid'):
   with self.subTest(stage=stage):
    self.store.db.execute('DELETE FROM workflow_resource_slot');self.store.db.execute('DELETE FROM workflow_stage_claim')
    self.store.db.execute('DELETE FROM workflow_stage_run');self.store.db.execute('DELETE FROM workflow_run')
    runs=self.seed(stage)
    selected=claim_ready(self.store,self.root,runs,self.policy,'scheduler-14-markets',accounts=self.accounts,worker_pid=111)
    self.assertEqual(len(selected['claimed']),1)
    self.assertEqual(self.store.db.execute("SELECT count(*) FROM workflow_resource_slot WHERE resource_key='platform:global'").fetchone()[0],1)
    waiting=claim_ready(self.store,self.root,runs,self.policy,'scheduler-14-markets',accounts=self.accounts,worker_pid=111)
    self.assertEqual(waiting['claimed'],[])

 def test_kalodata_runs_beside_another_markets_platform_stage(self):
  runs=[]
  for market,stage in (('it','catalog'),('br','oecid'),('uk','kalodata')):
   self.store.db.execute("INSERT INTO workflow_run(run_id,market,trigger_source,scheduled_at,applicable_sources_json,config_revision,state,started_at) VALUES(?,?,?,?,?,?,?,?)",
                         (f'run-{market}',market,'schedule',100,'[]',0,'queued',100))
   self.store.db.execute('INSERT INTO workflow_stage_run(stage_run_id,run_id,stage,position,state) VALUES(?,?,?,?,?)',
                         (f'stage-{market}',f'run-{market}',stage,0,'queued'))
   runs.append({'runId':f'run-{market}','market':market,'state':'queued','stages':[{'stageRunId':f'stage-{market}','stage':stage,'position':0,
                 'state':'queued','checkpoint':{},'outputGenerationId':None}]})
  selected=claim_ready(self.store,self.root,runs,self.policy,'scheduler-mixed-stages',accounts=self.accounts,worker_pid=111)
  self.assertEqual(sorted(row['run']['market'] for row in selected['claimed']),['br','uk'])

 def test_kalodata_has_two_global_slots_and_a_blocked_market_does_not_hold_them(self):
  runs=self.seed('kalodata',blocked_market='br')
  selected=claim_ready(self.store,self.root,runs,self.policy,'scheduler-kalodata',accounts=self.accounts,worker_pid=111)
  self.assertEqual(len(selected['claimed']),2)
  self.assertNotIn('br',{row['run']['market'] for row in selected['claimed']})
  self.assertEqual(self.store.db.execute("SELECT count(*) FROM workflow_resource_slot WHERE resource_key='kalodata:global'").fetchone()[0],2)

 def test_checkpoint_then_market_breaks_ties_among_markets_never_served(self):
  runs=self.seed(checkpoint_market='de')
  selected=claim_ready(self.store,self.root,runs,self.policy,'scheduler-fair-order',max_parallel=2,accounts=self.accounts,worker_pid=111)
  self.assertEqual([row['run']['market'] for row in selected['claimed']],['de'])
  ticket=selected['claimed'][0]['ticket'];release(self.store,ticket['stageRunId'],ticket['ownerId'],ticket['fence'])
  for run in runs:  # the scheduler reloads runs each tick; the claimed stage is no longer queued
   if run['market']=='de':run['stages'][0]['state']='running'
  following=claim_ready(self.store,self.root,runs,self.policy,'scheduler-fair-order',max_parallel=2,accounts=self.accounts,worker_pid=111)
  self.assertEqual([row['run']['market'] for row in following['claimed']],['be'])

 def test_a_newly_enabled_market_does_not_keep_the_platform_slot_from_others(self):
  # MY just ran its first catalog; its next never-succeeded TapLink stage waits behind BR's OECID,
  # which has waited longer since BR was last served.
  for market,stage,served in (('br','oecid',60.0),('my','taplink_prepare',95.0)):
   self.store.db.execute("INSERT INTO workflow_run(run_id,market,trigger_source,scheduled_at,applicable_sources_json,config_revision,state,started_at) VALUES(?,?,?,?,?,?,?,?)",
                         (f'run-{market}',market,'schedule',50,'[]',0,'running',50))
   self.store.db.execute("INSERT INTO workflow_stage_run(stage_run_id,run_id,stage,position,state,started_at,finished_at) VALUES(?,?,?,?,?,?,?)",
                         (f'done-{market}',f'run-{market}','kalodata' if market=='br' else 'catalog',0,'completed',served,served+4))
   self.store.db.execute('INSERT INTO workflow_stage_run(stage_run_id,run_id,stage,position,state) VALUES(?,?,?,?,?)',
                         (f'stage-{market}',f'run-{market}',stage,1,'queued'))
  runs=[{'runId':f'run-{m}','market':m,'state':'running','stages':[{'stageRunId':f'stage-{m}','stage':st,'position':1,'state':'queued','checkpoint':{},'outputGenerationId':None}]}
        for m,st in (('my','taplink_prepare'),('br','oecid'))]
  selected=claim_ready(self.store,self.root,runs,self.policy,'scheduler-turns',accounts=self.accounts,worker_pid=111)
  self.assertEqual([row['run']['market'] for row in selected['claimed']],['br'])

 def test_market_and_shared_account_slots_are_explicit(self):
  self.assertEqual(resources(self.root,'my','catalog',self.policy,accounts=self.accounts),[('workflow:my',1),('supply:acc6',1),('platform:global',1)])
  self.assertEqual(resources(self.root,'my','oecid',self.policy,accounts=self.accounts),[('workflow:my',1),('communications:acc26',1),('platform:global',1)])
  self.assertEqual(resources(self.root,'my','kalodata',self.policy,accounts=self.accounts),[('workflow:my',1),('kalodata:global',2)])


if __name__=='__main__':unittest.main()
