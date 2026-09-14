import json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.batch_task_service import TaskService,read_local_preparation,in_scope
from lib.batch_tasks import BatchError,normalize_spec
from lib.second_cycle import CycleStore,encoded,digest
from lib.cycle_materials import Materials,name_key
from lib.cycle_delivery import Deliveries
from test_second_cycle import offer,edge,NOW

class TaskServiceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'tasks';self.now=NOW;self.s=TaskService(self.path,lambda:self.now)
  self.spec={'institution':'bjn-local-research','market':'it','target':3000,'startDate':'2026-09-15','startTime':'22:00','endTime':'01:00'}
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def create(self,key='one',**changes):
  p=self.s.preview(self.spec|changes);return self.s.confirm(p['token'],key)
 def sample(self,spec):return ({'state':'waiting_adapters','candidates':1,'blockers':['task_taplink_adapter_pending'],'executionAllowed':False},[{'oec':'123','institution':spec['institution'],'market':spec['market'],'pid':'1729480033890900437','materialKey':'card1','checks':dict(source=True,identity=True,relationship=True,offer=True,name=True,taplink=False)}])
 def test_speed_decays_to_zero_after_no_candidate_growth(self):
  t=self.create()
  self.s.tasks.event(t['id'],'preparation_observed',{'candidates':10})
  self.now+=60;self.s.tasks.event(t['id'],'preparation_observed',{'candidates':30})
  self.assertEqual(self.s.preparation_speed(t['id'])['perMinute'],20)
  self.now+=360;self.assertEqual(self.s.preparation_speed(t['id'])['perMinute'],0)
 def test_preview_does_not_create_task_and_requires_actual_date_scope(self):
  p=self.s.preview(self.spec);self.assertEqual(p['spec']['reserve'],300);self.assertEqual(self.s.listing()['tasks'],[])
  self.assertFalse(p['policy']['fullManagedStockRequired']);self.assertFalse(p['executionConnected'])
  for changes in ({'startDate':None},{'market':'mx'},{'institution':'other'}):
   with self.assertRaises(BatchError):self.s.preview(self.spec|changes)
 def test_confirm_is_atomic_idempotent_and_pins_policy(self):
  t=self.create();self.assertEqual(t['id'],self.create()['id']);self.assertEqual(len(self.s.listing()['tasks']),1)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM batch_preparation_run').fetchone()[0],1)
  with self.assertRaises(BatchError):self.create(target=5000)
  self.assertEqual(self.s.detail(t['id'])['spec']['target'],3000)
  self.assertEqual(self.s.detail(t['id'])['policy']['templateVersion'],4)
 def test_restart_retains_outbox_and_local_checks_never_authorize(self):
  t=self.create();self.s.close();self.s=TaskService(self.path,lambda:self.now)
  self.s.tick(self.sample);d=self.s.detail(t['id']);self.assertEqual(d['preparation']['candidates'],1)
  self.assertEqual(d['state'],'preparing');self.assertFalse(d['executionConnected']);self.assertEqual(self.s.tasks.readiness(t['id'])['ready'],0)
 def test_pause_during_read_discards_observation_and_resume_is_explicit(self):
  t=self.create()
  def race(spec):self.s.control(t['id'],'pause',t['revision']);return self.sample(spec)
  self.s.tick(race);self.assertIsNone(self.s.detail(t['id'])['preparation']);self.assertFalse(self.s.tick(self.sample))
  with self.assertRaisesRegex(BatchError,'revision_conflict'):self.s.control(t['id'],'resume',1)
  d=self.s.detail(t['id']);self.s.control(t['id'],'resume',d['revision']);self.assertTrue(self.s.tick(self.sample))
 def test_unchanged_retry_does_not_append_events_or_overwrite_members(self):
  t=self.create();self.s.tick(self.sample);d=self.s.detail(t['id']);before=self.s.db.total_changes
  self.now+=31;self.s.tick(self.sample);after=self.s.detail(t['id'])
  self.assertEqual(d['revision'],after['revision']);self.assertEqual(len(d['events']),len(after['events']));self.assertLess(self.s.db.total_changes-before,4)
 def test_data_failure_and_out_of_scope_member_do_not_become_ready(self):
  t=self.create()
  def broken(spec):raise OSError('sensitive internal message')
  self.s.tick(broken);d=self.s.detail(t['id']);self.assertEqual(d['preparation']['blockers'],['local_source_unavailable']);self.assertNotIn('sensitive',json.dumps(d))
  self.now+=31
  def wrong(spec):r,m=self.sample(spec);m[0]['institution']='other';return r,m
  self.s.tick(wrong);self.assertEqual(self.s.tasks.readiness(t['id'])['ready'],0)
 def test_scope_priority_and_restricted_pids(self):
  old=self.create();self.now+=1;new=self.create('new',target=5000)
  self.s.control(new['id'],'priority',new['revision'],10)
  self.s.tick(self.sample);self.assertIsNotNone(self.s.detail(new['id'])['preparation']);self.assertIsNone(self.s.detail(old['id'])['preparation'])
  self.assertFalse(in_scope(normalize_spec(self.spec|{'productScope':{'kind':'pids','values':['1729480033890900437']}}),{'pid':'other'}))

class LocalPreparationTests(unittest.TestCase):
 def setUp(self):
  import time
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'var').mkdir()
  self.s=CycleStore(self.root/'var/second-cycle.sqlite');self.p=self.s.plan('bjn-local-research','it');Materials(self.s);Deliveries(self.s)
  self.o=offer(title='Cuscino',campaignId='2',endAt=time.time()+90*86400,managementType='full_managed',managementEvidenceRef='official-global-source',stock=None)
  self.s.publish(self.p,'source',NOW,[self.o]);self.s.import_edges(self.p,[edge(sourceKind='kalodata_http'),edge(source='e2',sourceKind='kalodata_http',observedAt=NOW+1)])
  self.s.db.executemany('INSERT INTO cycle_identity_resolution VALUES(?,?,?,?,?)',[(self.p,'e1','c1','123','proof'),(self.p,'e2','c1','123','proof')])
  self.ids=sqlite3.connect(self.root/'var/creator-identities.sqlite');self.ids.execute('CREATE TABLE creator_identity(market,creator_id,oec_id,handle_conflict)');self.ids.execute("INSERT INTO creator_identity VALUES('it','c1','123',0)");self.ids.commit()
  self.spec=normalize_spec({'institution':'bjn-local-research','market':'it','target':5000,'startTime':'22:00','endTime':'01:00'})
 def tearDown(self):self.s.close();self.ids.close();self.tmp.cleanup()
 def test_full_managed_without_stock_and_no_daily_cap(self):
  report,members=read_local_preparation(self.root,self.spec)
  self.assertEqual(report['required'],5500);self.assertEqual(report['candidates'],1);self.assertEqual(report['candidateGap'],5499);self.assertEqual(len(members),1)
  self.assertEqual(self.s._plan(self.p)['capacity_new'],0);self.assertFalse(members[0]['checks']['taplink'])
 def test_material_observation_is_not_task_verification(self):
  self.s.db.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?,?)',(name_key(self.o),'1','it-IT','Cuscino',encoded({'shortNameZh':'枕头'}),'job'))
  card={'state':'verified_read_only','pid':'1','sourceCampaignId':'2','creatorPercent':'12','listId':'999','evidenceRefs':['http-evidence']}
  self.s.db.execute('INSERT INTO cycle_card_check VALUES(?,?,?,?)',(self.p,'o1',digest(self.o),encoded(card)))
  report,members=read_local_preparation(self.root,self.spec)
  self.assertEqual(report['named'],1);self.assertEqual(report['cardsLocated'],1);self.assertEqual(report['verifiedReady'],0)
 def test_consumed_edge_not_reintroduced_via_another_eligible_edge(self):
  self.s.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',('sent',self.p,'c1','123','1','e1','{}',NOW,NOW+30,'confirmed'))
  report,members=read_local_preparation(self.root,self.spec);self.assertEqual(report['candidates'],1);self.assertEqual(members[0]['sourceId'],'e2')
  self.s.db.execute("UPDATE cycle_delivery SET state='unknown' WHERE id='sent'")
  self.assertEqual(read_local_preparation(self.root,self.spec)[0]['candidates'],0)
 def test_rejected_and_scope_mismatch_excluded(self):
  self.s.db.execute("UPDATE relationship SET rejected=1 WHERE plan_id=?",(self.p,))
  self.assertEqual(read_local_preparation(self.root,self.spec)[0]['candidates'],0)
  self.s.db.execute("UPDATE relationship SET rejected=0 WHERE plan_id=?",(self.p,))
  restricted=self.spec|{'productScope':{'kind':'campaigns','values':['other']}}
  self.assertEqual(read_local_preparation(self.root,restricted)[0]['candidates'],0)

if __name__=='__main__':unittest.main()
