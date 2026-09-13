import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,assess_offer
from lib.global_source import GlobalSources
from lib.cycle_management import sync_full_managed
from test_second_cycle import offer,NOW
class ManagementTests(unittest.TestCase):
 def test_only_verified_matching_official_source_changes_policy(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);cycle_path=root/'cycle';source_path=root/'source';scope={'market':'it','account':'acc6','institutionFingerprint':'a'*64}
   a='1729000000000000001';b='1729000000000000002'
   with CycleStore(cycle_path,lambda:NOW) as c:
    plan=c.plan('bjn-local-research','it');c.publish(plan,'source',NOW,[offer(a,stock=None),offer(b,stock=None)])
   g=GlobalSources(source_path,clock=lambda:NOW);g.start('one',scope);g.page('one',1,{'products':[{'product_id':a,'title':'test'}],'has_more':False,'total':1})
   self.assertEqual(sync_full_managed(cycle_path,source_path,scope)['fullManagedPids'],0)
   g.finish_session('one',True);g.close()
   self.assertEqual(sync_full_managed(cycle_path,source_path,scope|{'institutionFingerprint':'b'*64})['fullManagedPids'],0)
   self.assertEqual(sync_full_managed(cycle_path,source_path,scope)['fullManagedPids'],1)
   with CycleStore(cycle_path,lambda:NOW) as c:
    rows={o['pid']:assess_offer(o,NOW) for _,o in c._offers(plan)}
    self.assertTrue(rows[a]['eligible']);self.assertFalse(rows[a]['stockRequired']);self.assertFalse(rows[b]['eligible'])
if __name__=='__main__':unittest.main()
