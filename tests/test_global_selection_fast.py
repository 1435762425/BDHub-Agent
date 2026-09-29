import sys,unittest,tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.global_selection import READBACK_DELAYS,Selection
from lib.global_selection_fast import Gate,requires_review,run

class FastTests(unittest.TestCase):
 def test_readback_waits_for_platform_final_consistency(self):
  self.assertEqual(READBACK_DELAYS,(0,1,3,30,120))
 def test_shared_gate_spaces_dispatches(self):
  at=[1.]
  g=Gate(8,clock=lambda:at[0],sleep=lambda n:at.__setitem__(0,at[0]+n))
  times=[]
  for _ in range(5):g.acquire();times.append(at[0])
  self.assertEqual(times,[1,1.125,1.25,1.375,1.5])
  with self.assertRaises(ValueError):Gate(9)
 def test_parallel_writes_follow_durable_intents_and_exact_readback(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root);sent=set();pid='1729480000000000001';pid2='1729480000000000002'
   with ledger.db:
    for p in [pid,pid2]:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r',p,'pending','{}',1))
   items=ledger.items('r')
   class Remote:
    session=SimpleNamespace(close=lambda:None)
    def fork_lane(self,pace):return self
    def copy_session_from(self,other):pass
    def _params(self):return {}
    def selected_page(self,page,pids):return {'total':len(sent&set(pids)),'items':[{'campaign_product':{'product_id':p,'total_commission_percent':'1200'},'campaign_info':{'campaign_id':'1234567890123456789','crs_campaign_type':9}} for p in pids if p in sent]}
    def opportunity_page(self,page,global_only,pids):return {'has_more':False,'products':[{'product_id':p,'sales':'300 已售','product_rating':4,'commission_rate':'1200','open_collab_rate':'1000','fs_is_selected':False} for p in pids]}
    def _xhr(self,**kwargs):return SimpleNamespace(has_turing=False,payload={'data':{'product_campaign_detail':[{'campaign':{'campaign_id':'1234567890123456789','crs_campaign_type':9,'promotion_start_time':'1000','promotion_end_time':'9999999999999','commission':'1200'},'open_collab_rate':'1000'}]}})
    def require_read(self,r):return r.payload
    def select_product(self,p,cid):
     assert all(i['state'] in ('submitting','awaiting_verification') for i in items)
     assert p not in sent;sent.add(p)
     return SimpleNamespace(http_status=200,code=0,has_turing=False,ambiguous=False,system_error_3=False)
   with patch.object(Gate,'acquire',lambda self:None):run(ledger,'r',Remote(),{},items,{},lambda:None,lambda:False,width=2,qps=8)
   self.assertEqual(ledger.status('r'),{'confirmed':2});self.assertEqual(sent,{pid,pid2});ledger.db.close()
 def test_batch_size_is_explicitly_bounded(self):
  with self.assertRaisesRegex(ValueError,'selection_batch_size_outside_test_scope'):
   run(None,'r',None,{},[],{},lambda:None,lambda:False,batch_size=101)
 def test_a_fatal_transport_signal_does_not_stop_after_exact_readback_confirms_it(self):
  self.assertFalse(requires_review([{'state':'confirmed','payload':{}}]))
  self.assertFalse(requires_review([{'state':'result_unknown','payload':{'platformVerification':'passed'}}]))
  self.assertTrue(requires_review([{'state':'result_unknown','payload':{'receipt':{'ambiguous':True}}}]))
if __name__=='__main__':unittest.main()
