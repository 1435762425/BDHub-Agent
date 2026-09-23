import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,assess_offer
from lib.global_source import GlobalSources,list_request
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
 def test_operator_accepted_partial_head_keeps_full_managed_classification(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);cycle_path=root/'cycle';source_path=root/'source';scope={'market':'it','account':'acc6','institutionFingerprint':'a'*64}
   with CycleStore(cycle_path,lambda:NOW) as c:c.plan('bjn-local-research','it')
   g=GlobalSources(source_path,clock=lambda:NOW)
   try:
    categories=[{'category_id':'600001','name':'家居','is_leaf':False},{'category_id':'600002','name':'玩具','is_leaf':False}]
    g.start_partitioned('accepted',scope,categories);g.next_partition('accepted')
    g.partition_page('accepted','600001',1,{'products':[{'product_id':'1729000000000000001','title':'One'}],'has_more':False,'total':1},request_payload=list_request(1,category_id='600001'))
    g.next_partition('accepted');g.partition_page('accepted','600002',1,{'products':[{'product_id':'1729000000000000002','title':'Two'}],'has_more':True,'total':2},request_payload=list_request(1,category_id='600002'))
    g.finish_session('accepted',True);g.accept_partial_snapshot('accepted')
   finally:g.close()
   self.assertEqual(sync_full_managed(cycle_path,source_path,scope)['fullManagedPids'],2)
if __name__=='__main__':unittest.main()
