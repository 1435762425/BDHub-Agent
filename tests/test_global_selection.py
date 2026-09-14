import sys,unittest,tempfile,json,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.global_selection import sales,assess,choose_campaign,Selection,observations,selected_rows,retryable_verification_rejection
class SelectionTests(unittest.TestCase):
 def test_inclusive_sales_and_percentage_point_boundary(self):
  p={'sales':'300 已售','product_rating':4,'commission_rate':'1200','open_collab_rate':'1000'}
  self.assertTrue(assess(p)['eligible'])
  for field,value in [('sales','299 已售'),('product_rating',3.9),('commission_rate','1199'),('open_collab_rate',None)]:self.assertFalse(assess(p|{field:value})['eligible'])
  self.assertEqual(sales('6.831 已售'),6831);self.assertEqual(sales('12,2K 已售'),12200)
  self.assertTrue(assess(p|{'stock':0})['eligible'])
 def test_current_campaign_type9_and_expiry(self):
  e={'campaign':{'campaign_id':'1234567890123456789','crs_campaign_type':9,'promotion_start_time':'1000','promotion_end_time':'3000','commission':'1200'},'open_collab_rate':'1000'}
  self.assertEqual(choose_campaign([e],2),e);self.assertIsNone(choose_campaign([e],3))
 def test_attempt_never_resubmitted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();s=Selection(root)
   with s.db:s.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','pending','{}',1))
   item=s.items('r')[0];s.begin(item,{'campaign':{}})
   with self.assertRaises(ValueError):s.begin(item,{'campaign':{}})
   s.db.close()
 def test_observations_are_scoped_and_do_not_confirm_unknowns(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();s=Selection(root)
   with s.db:
    s.db.execute('INSERT INTO intake_run VALUES(?,?,?,?)',('r','{}','source',1))
    s.db.executemany('INSERT INTO intake_item VALUES(?,?,?,?,?)',[('r','p','confirmed','{}',2),('r','q','result_unknown','{}',3)])
   summary,items=observations(root/'var','source')
   self.assertEqual(summary['states'],{'confirmed':1,'result_unknown':1});self.assertEqual(items['q']['state'],'result_unknown')
   self.assertEqual(observations(root/'var','other'),(None,{}));s.db.close()
 def test_readback_rejects_unrequested_pid(self):
  class Remote:
   def selected_page(self,*_,**__):return {'total':1,'items':[{'campaign_product':{'product_id':'wrong'},'campaign_info':{'campaign_id':'c'}}]}
  with self.assertRaises(ValueError):selected_rows(Remote(),['wanted'])
 def test_only_proven_verification_rejection_can_continue(self):
  r={'http':200,'code':10000,'verification':True,'ambiguous':False};i={'pid':'p','state':'result_unknown','payload':{'receipt':r}}
  fresh={'product_id':'p','fs_is_selected':False,'sales':'300 已售','product_rating':4,'commission_rate':'1200','open_collab_rate':'1000'}
  self.assertTrue(retryable_verification_rejection(i,set(),fresh))
  self.assertFalse(retryable_verification_rejection(i,{'p'},fresh))
  self.assertFalse(retryable_verification_rejection(i,set(),fresh|{'fs_is_selected':True}))
  for key,value in [('code',0),('http',0),('ambiguous',True),('verification',False)]:
   self.assertFalse(retryable_verification_rejection(i|{'payload':{'receipt':r|{key:value}}},set(),fresh))
  self.assertFalse(retryable_verification_rejection(i|{'payload':{'receipt':r,'priorAttempts':[{},{}]}},set(),fresh))
if __name__=='__main__':unittest.main()
