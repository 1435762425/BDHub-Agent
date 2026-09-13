import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,encoded,digest
from lib.cycle_materials import Materials,name_key
from lib.batch_preparation import inspect_preparation
from test_second_cycle import offer,edge,NOW
class PreparationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=CycleStore(Path(self.tmp.name)/'db',lambda:NOW);self.p=self.s.plan('bjn','it');Materials(self.s)
  self.o=offer(title='Cuscino',campaignId='2');self.s.publish(self.p,'source',NOW,[self.o]);self.s.import_edges(self.p,[edge(sourceKind='kalodata_http'),edge(source='e2',sourceKind='kalodata_http')])
  self.s.db.executemany('INSERT INTO cycle_identity_resolution VALUES(?,?,?,?,?)',[(self.p,'e1','c1','123','proof'),(self.p,'e2','c1','123','proof')])
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def test_dedup_and_missing_taplink_are_visible(self):
  r=inspect_preparation(self.s,self.p,lambda *a:True,3000);self.assertEqual(r['required'],3300);self.assertEqual(r['identityCandidates'],1);self.assertEqual(r['candidateGap'],3299);self.assertEqual(r['taplinksToCheckOrCreate'],1);self.assertEqual(r['localMaterialsComplete'],0)
 def test_current_materials_are_not_remote_permission(self):
  self.s.db.execute('INSERT INTO cycle_product_name VALUES(?,?,?,?,?,?)',(name_key(self.o),'1','it-IT','Cuscino',encoded({'shortNameZh':'枕头'}),'job'))
  card={'state':'verified_read_only','pid':'1','sourceCampaignId':'2','creatorPercent':'12','listId':'999'}
  self.s.db.execute('INSERT INTO cycle_card_check VALUES(?,?,?,?)',(self.p,'o1',digest(self.o),encoded(card)))
  r=inspect_preparation(self.s,self.p,lambda *a:True,5000);self.assertEqual(r['localMaterialsComplete'],1);self.assertFalse(r['executionAllowed']);self.assertFalse(r['liveEligibilityChecked']);self.assertFalse(r['taskCreated'])
 def test_rejected_and_unverified_identity_excluded(self):
  self.assertEqual(inspect_preparation(self.s,self.p,lambda *a:False,3000)['identityCandidates'],0)
  self.s.control(self.p,'reject',1,'auto','c1',True)
  self.assertEqual(inspect_preparation(self.s,self.p,lambda *a:True,3000)['identityCandidates'],0)
if __name__=='__main__':unittest.main()
