import sys,tempfile,unittest,sqlite3,json
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.creator_discovery import CreatorDiscoveryStore
from lib.cycle_identity import IdentityBridge
from test_second_cycle import NOW,offer,edge
class BridgeTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.s=CycleStore(self.root/'second-cycle.sqlite',lambda:NOW);self.p=self.s.plan('test','it')
  self.d=CreatorDiscoveryStore(self.root);self.ids=self.root/'creator-identities.sqlite'
  with closing(sqlite3.connect(self.ids)) as c,c:c.execute('CREATE TABLE identity_observation(creator_id TEXT,market TEXT,oec_id TEXT,evidence_ref TEXT)')
  self.b=IdentityBridge(self.s,self.d,self.ids)
  self.s.publish(self.p,'source',NOW,[offer(),offer('2')]);self.s.import_edges(self.p,[edge(person=None,sourceKind='kalodata_http',sourceHandle='hello'),edge('2',person=None,source='e2',sourceKind='kalodata_http',sourceHandle='hello')])
 def tearDown(self):self.s.close();self.d.close();self.t.cleanup()
 def resolve(self,proof=True):
  batch=self.s.db.execute('SELECT batch_id FROM cycle_identity_outbox').fetchone()[0];item=self.d.detail(batch)['items'][0]
  with self.d.transaction():self.d._db.execute("UPDATE discovery_item SET status='completed',creator_id='c1',oec_id='123',outcome='created' WHERE id=?",(item['id'],))
  if proof:
   with closing(sqlite3.connect(self.ids)) as c,c:c.execute('INSERT INTO identity_observation VALUES(?,?,?,?)',('c1','it','123','creator-discovery:'+item['id']+':'+('a'*64)+':discovery-result'))
 def test_freeze_and_submit_deduplicate_handles(self):
  self.assertIsNotNone(self.b.freeze(self.p));self.assertIsNone(self.b.freeze(self.p));self.b.dispatch(self.p);self.assertEqual(self.b.dispatch(self.p),[])
  self.assertEqual(self.d._db.execute('SELECT count(*) FROM discovery_item').fetchone()[0],1)
 def test_task_scoped_handoff_does_not_dispatch_other_sources(self):
  first=self.b.freeze(self.p,source_ids=['e1']);second=self.b.freeze(self.p,source_ids=['e2'])
  self.b.dispatch(self.p,outbox_ids=[first])
  self.assertEqual(self.s.db.execute('SELECT batch_id FROM cycle_identity_outbox WHERE id=?',(second,)).fetchone()[0],None)
  self.assertEqual(json.loads(self.s.db.execute('SELECT payload FROM cycle_identity_outbox WHERE id=?',(first,)).fetchone()[0])['edges'][0]['sourceId'],'e1')
  self.assertIsNone(self.b.freeze(self.p,source_ids=[]))
 def test_recover_submit_before_local_ack(self):
  self.b.freeze(self.p);old=self.d.submit
  def crash(*a):old(*a);raise KeyboardInterrupt()
  self.d.submit=crash
  with self.assertRaises(KeyboardInterrupt):self.b.dispatch(self.p)
  self.d.submit=old;self.b.dispatch(self.p);self.assertEqual(self.d._db.execute('SELECT count(*) FROM discovery_batch').fetchone()[0],1)
 def test_verified_mapping_preserves_raw_source_and_replays(self):
  self.b.freeze(self.p);self.b.dispatch(self.p);self.resolve();r=self.b.reconcile(self.p)
  self.assertEqual(r['newBindings'],2);self.assertEqual(self.b.reconcile(self.p)['newBindings'],0)
  self.assertEqual(self.s.status(self.p)['relationships'],1);self.assertEqual(self.s.status(self.p)['opportunities'],2)
  self.assertIsNone(json.loads(self.s.db.execute('SELECT payload FROM source_edge LIMIT 1').fetchone()[0])['creatorId'])
  self.assertEqual(self.s.pending_identity_count(self.p,'2026-09-13'),0)
 def test_missing_proof_does_not_bind(self):
  self.b.freeze(self.p);self.b.dispatch(self.p);self.resolve(False)
  with self.assertRaisesRegex(CycleError,'identity_proof_missing'):self.b.reconcile(self.p)
  self.assertEqual(self.s.status(self.p)['relationships'],0)
 def test_paused_plan_does_not_submit(self):
  self.b.freeze(self.p);self.s.control(self.p,'pause',1,'paused');self.assertEqual(self.b.dispatch(self.p),[])
  self.assertEqual(self.d._db.execute('SELECT count(*) FROM discovery_batch').fetchone()[0],0)
 def test_coordinator_recovers_gap_before_outbox_creation(self):
  import importlib.util
  path=Path(__file__).resolve().parents[1]/'scripts/creator-profile-refresh.py'
  spec=importlib.util.spec_from_file_location('identity_coordinator_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  plan=self.s.plan('bjn-local-research','it');self.s.import_edges(plan,[edge(person=None,sourceKind='kalodata_http',sourceHandle='gap')])
  result=module.reconcile_cycle(self.root,self.d);self.assertNotEqual(result.get('status'),'blocked')
  module.reconcile_cycle(self.root,self.d)
  self.assertEqual(self.d._db.execute('SELECT count(*) FROM discovery_batch').fetchone()[0],1)
 def test_unmatched_releases_pending_capacity_without_deleting(self):
  self.b.freeze(self.p);self.b.dispatch(self.p)
  with self.d.transaction():self.d._db.execute("UPDATE discovery_item SET status='unresolved',reason='no_exact_handle'")
  self.b.reconcile(self.p)
  self.assertEqual(self.s.pending_identity_count(self.p,'2026-09-13'),0)
  self.assertEqual(self.s.status(self.p)['sourceEdges'],2);self.assertEqual(self.s.status(self.p)['relationships'],0)
 def test_resume_after_binding_before_projection(self):
  self.b.freeze(self.p);self.b.dispatch(self.p);self.resolve();original=self.s.project_current_offers
  self.s.project_current_offers=lambda *a:(_ for _ in ()).throw(RuntimeError('interrupted'))
  with self.assertRaises(RuntimeError):self.b.reconcile(self.p)
  self.s.project_current_offers=original;self.assertEqual(self.b.reconcile(self.p)['newBindings'],0)
  self.assertEqual(self.s.status(self.p)['opportunities'],2)
 def test_existing_rejection_survives(self):
  self.s.import_edges(self.p,[edge(source='known')]);self.s.control(self.p,'reject',1,'auto','c1',True)
  self.b.freeze(self.p);self.b.dispatch(self.p);self.resolve();self.b.reconcile(self.p)
  self.assertEqual(self.s.status(self.p)['eligibleUniqueCreators'],0)
if __name__=='__main__':unittest.main()
