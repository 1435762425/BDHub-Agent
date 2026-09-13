import sys,unittest,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.global_source import GlobalSources,GlobalSourceError,list_request,clean_details,clean_product
from lib.second_cycle import digest

def product(n):return {'product_id':str(1729000000000000000+n),'title':'Product','commission_rate':'1400','open_collab_rate':'900','contact_info':{'secret':'PRIVATE'},'fs_is_selected':False}
def page(ids,more=False,total=None):return {'products':[product(n) for n in ids],'has_more':more,'total':len(ids) if total is None else total}
class SourceTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.path=Path(self.t.name)/'db';self.now=1000;self.s=GlobalSources(self.path,clock=lambda:self.now);self.scope={'market':'it','account':'acc6','institutionFingerprint':'a'*64};self.s.start('one',self.scope)
 def tearDown(self):self.s.close();self.t.cleanup()
 def test_exact_source_and_no_private_fields(self):
  self.assertEqual(list_request(1)['filter']['campaign_type'],[8]);self.assertEqual(list_request(1)['page_size'],15)
  first=list_request(1);first['filter']['campaign_type'].append(9);self.assertEqual(list_request(2)['filter']['campaign_type'],[8])
  p=clean_product(product(1));self.assertNotIn('PRIVATE',json.dumps(p));self.assertNotIn('stock',p)
 def test_pages_durable_replay_and_completed_head_requires_identity(self):
  a=page([1,2],True,3);self.s.page('one',1,a);self.s.close();self.s=GlobalSources(self.path,clock=lambda:self.now)
  self.assertEqual(self.s.get('one')['next_page'],2);self.s.page('one',1,a);self.s.page('one',2,page([3],False,3));self.assertFalse(self.s.status('one')['published'])
  self.s.finish_session('one',True);self.assertTrue(self.s.status('one')['published']);self.assertFalse(self.s.status('one')['executionAllowed'])
 def test_repeated_page_and_empty_more_do_not_advance(self):
  self.s.page('one',1,page([1,2],True,10000))
  with self.assertRaises(GlobalSourceError):self.s.page('one',2,page([1,2],True,10000))
  with self.assertRaises(GlobalSourceError):self.s.page('one',2,page([],True,10000))
  self.assertEqual(self.s.get('one')['next_page'],2)
 def test_aligned_tail_covers_exact_remaining_offset(self):
  request=list_request(667,10000);self.assertEqual(request['page_size'],10);self.assertEqual((request['page']-1)*request['page_size'],9990);self.assertEqual(request['filter']['campaign_type'],[8])
 def test_boundary_recovery_retains_9990_and_confirms_exact_tail(self):
  # Simulate persisted accepted pages from the previous collector version.
  for n in range(1,667):self.s.page('one',n,page(range((n-1)*15+1,n*15+1),True,10000))
  self.s.blocked('one','source_remote_rejected')
  with self.assertRaises(GlobalSourceError):self.s.retry_boundary_tail('one',{'http':200,'code':0,'page':667,'verification':False})
  self.s.retry_boundary_tail('one',{'http':200,'code':98001004,'page':667,'verification':False})
  tail=page(range(9991,10001),False,10000)
  with self.assertRaises(GlobalSourceError):self.s.page('one',667,tail)
  self.s.page('one',667,tail,request_payload=list_request(667,10000));self.s.finish_session('one',True)
  result=self.s.status('one');self.assertEqual(result['products'],10000);self.assertEqual(result['pages'],667);self.assertTrue(result['published'])
 def test_total_is_not_a_stop_condition(self):
  self.s.page('one',1,page([1],True,1));self.assertEqual(self.s.get('one')['state'],'collecting')
 def test_partial_end_never_overwrites_complete_head(self):
  self.s.page('one',1,page([1]));self.s.finish_session('one',True);self.now+=10;self.s.start('two',self.scope)
  self.s.page('two',1,page([2],False,10000));self.s.finish_session('two',True)
  self.assertEqual(self.s.status('two')['state'],'partial');self.assertTrue(self.s.status('one')['published'])
  display=self.s.products();self.assertEqual(display['displayRunId'],'one');self.assertEqual(display['items'][0]['pid'],product(1)['product_id'])
 def test_scope_changes_and_foreign_market_rejected(self):
  with self.assertRaises(GlobalSourceError):self.s.start('one',self.scope|{'institutionFingerprint':'b'*64})
  with self.assertRaises(GlobalSourceError):self.s.start('two',self.scope|{'market':'mx'})
 def test_only_one_active_query_per_scope(self):
  with self.assertRaisesRegex(GlobalSourceError,'already_collecting'):self.s.start('two',self.scope)
 def test_bad_page_atomic(self):
  for data in (page([1,1]),{'products':[product(1)],'has_more':'false','total':1},page([1],False,True)):
   with self.assertRaises(GlobalSourceError):self.s.page('one',1,data)
  self.assertEqual(self.s.status()['products'],0)
 def test_detail_is_bound_to_exact_listing_and_preserves_current_campaign(self):
  self.s.page('one',1,page([1]));p=self.s.db.execute('SELECT * FROM global_source_product').fetchone()
  rows=[{'campaign':{'campaign_id':'222','crs_campaign_type':9,'commission':'1400'},'open_collab_rate':'900','contact_info':'PRIVATE'}]
  self.s.detail('one',p['pid'],p['fingerprint'],rows);self.assertEqual(self.s.status()['detailProducts'],1)
  self.assertNotIn('PRIVATE',self.s.db.execute('SELECT payload FROM global_source_detail').fetchone()[0])
  with self.assertRaises(GlobalSourceError):self.s.detail('one',p['pid'],'old',rows)
 def test_stock_and_pagination_remain_local_and_bound(self):
  self.s.page('one',1,page([1,2]));row=self.s.db.execute('SELECT * FROM global_source_product WHERE pid=?',(product(1)['product_id'],)).fetchone()
  offers=[{'pid':row['pid'],'catalogSource':'selected','stock':'200','assessment':{'eligible':True}}]
  self.s.stock('one',row['pid'],row['fingerprint'],offers,'proof')
  r=self.s.products('one',limit=1);self.assertEqual(r['totalMatches'],2);self.assertEqual(len(r['items']),1);self.assertTrue(r['items'][0]['stockChecked']);self.assertFalse(r['executionAllowed'])
  self.assertEqual(self.s.products('one',query=product(2)['product_id'])['totalMatches'],1)
  with self.assertRaises(GlobalSourceError):self.s.stock('one',row['pid'],'stale',offers,'proof')
 def test_identity_change_blocks_publication(self):
  self.s.page('one',1,page([1]));self.s.finish_session('one',False);self.assertEqual(self.s.status()['state'],'blocked');self.assertFalse(self.s.status()['published'])
if __name__=='__main__':unittest.main()
