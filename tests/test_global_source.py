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
  self.assertEqual(list_request(1,category_id='600001')['filter']['category_id'],['600001'])
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
 def test_plain_query_repairs_missing_unique_pid_without_replacing_pages(self):
  self.s.page('one',1,page([1,2],True,3));self.s.page('one',2,page([2],False,3))
  scope=self.s.partial_query_repair_scope('one');self.assertIn(2,scope['pages'])
  result=self.s.repair_query_page('one',2,page([3],False,3),list_request(2,3),scope['attempt'])
  self.assertEqual((result['added'],result['complete']),(1,True))
  self.s.finish_session('one',True);status=self.s.status('one')
  self.assertEqual((status['products'],status['state'],status['published']),(3,'completed',True))
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM global_source_page WHERE run_id=?',('one',)).fetchone()[0],2)
 def test_plain_query_stable_duplicate_requires_exact_replay(self):
  self.s.page('one',1,page([1,2],True,3));duplicate=page([2],False,3);self.s.page('one',2,duplicate)
  with self.assertRaisesRegex(GlobalSourceError,'stable_duplicate_evidence_missing'):
   self.s.accept_stable_query_duplicates('one')
  scope=self.s.partial_query_repair_scope('one')
  self.s.repair_query_page('one',2,duplicate,list_request(2,3),scope['attempt'])
  accepted=self.s.accept_stable_query_duplicates('one');self.assertEqual(accepted['duplicateRows'],1)
  self.s.finish_session('one',True);status=self.s.status('one')
  self.assertEqual((status['products'],status['reportedTotal'],status['stableDuplicateRows']), (2,3,1))
  self.assertEqual(status['coverage'],'current_query_endpoint_stable_duplicates')
 def test_scope_changes_and_foreign_market_rejected(self):
  with self.assertRaises(GlobalSourceError):self.s.start('one',self.scope|{'institutionFingerprint':'b'*64})
  with self.assertRaises(GlobalSourceError):self.s.start('two',self.scope|{'market':'mx'})
 def test_only_one_active_query_per_scope(self):
  with self.assertRaisesRegex(GlobalSourceError,'already_collecting'):self.s.start('two',self.scope)
  with self.assertRaisesRegex(GlobalSourceError,'already_collecting'):self.s.start('other_actor',self.scope|{'account':'acc9'})
 def test_new_account_keeps_one_source_head_and_preserves_history(self):
  self.s.page('one',1,page([1]));self.s.finish_session('one',True)
  old_hash=self.s.get('one')['scope_hash'];old_payload=self.s.get('one')['scope']
  self.now+=10;self.s.start('acc9_run',self.scope|{'account':'acc9'})
  self.assertEqual(self.s.get('acc9_run')['scope_hash'],old_hash)
  self.s.page('acc9_run',1,page([2],True,2));self.s.finish_session('acc9_run',True)
  self.assertEqual(self.s.products()['displayRunId'],'one')
  self.s.page('acc9_run',2,page([3],False,2));self.s.finish_session('acc9_run',True)
  self.assertEqual(self.s.products()['displayRunId'],'acc9_run')
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM global_source_head').fetchone()[0],1)
  self.assertEqual(self.s.get('one')['scope'],old_payload)
  self.assertEqual(self.s.get('acc9_run')['scope']['account'],'acc9')
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
 def test_category_partitions_finish_independently_and_publish_one_deduplicated_head(self):
  old_hash=self.s.get('one')['scope_hash'];self.s.blocked('one','fixture_end')
  categories=[{'category_id':'600001','name':'家居用品','is_leaf':False},
              {'category_id':'600002','name':'女装','is_leaf':False}]
  self.s.start_partitioned('bycat',self.scope,categories)
  self.assertEqual(self.s.get('bycat')['scope_hash'],old_hash)
  first=self.s.next_partition('bycat');self.assertEqual(first['category_id'],'600001')
  request=list_request(1,category_id='600001')
  first_page=page([1,2],False,2)
  self.s.partition_page('bycat','600001',1,first_page,request_payload=request)
  self.s.partition_page('bycat','600001',1,first_page,request_payload=request)
  second=self.s.next_partition('bycat');self.assertEqual(second['category_id'],'600002')
  self.s.partition_page('bycat','600002',1,page([2,3],False,2),
                        request_payload=list_request(1,category_id='600002'))
  before=self.s.status('bycat');self.assertEqual(before['state'],'completed');self.assertFalse(before['published'])
  self.s.finish_session('bycat',True);result=self.s.status('bycat')
  self.assertEqual((result['products'],result['reportedTotal'],result['categoryMemberships'],result['categoryOverlap']),(3,4,4,1))
  self.assertEqual((result['categoryCount'],result['categoriesCompleted'],result['pages']),(2,2,2))
  self.assertEqual(result['coverage'],'category_l1_endpoint_and_totals');self.assertTrue(result['published'])
 def test_operator_stop_preserves_unpublished_category_pages_and_closes_open_scope(self):
  self.s.blocked('one','fixture_end')
  categories=[{'category_id':'600001','name':'家居用品','is_leaf':False},
              {'category_id':'600002','name':'玩具','is_leaf':False}]
  self.s.start_partitioned('stoppedcat',self.scope,categories)
  self.s.next_partition('stoppedcat')
  self.s.partition_page('stoppedcat','600001',1,page([1],False,1),
                        request_payload=list_request(1,category_id='600001'))
  self.s.next_partition('stoppedcat')
  self.s.partition_page('stoppedcat','600002',1,page([2],True,3),
                        request_payload=list_request(1,category_id='600002'))
  before=self.s.status('stoppedcat')
  stopped=self.s.stop_unpublished_category('stoppedcat')
  self.assertEqual((stopped['state'],stopped['reason'],stopped['products'],stopped['pages']),
                   ('stopped','operator_stopped_category_collection',before['products'],before['pages']))
  self.assertFalse(stopped['published']);self.assertIsNone(stopped['nextCategory'])
  self.assertEqual(self.s.db.execute("SELECT state FROM global_source_partition WHERE run_id='stoppedcat' ORDER BY position").fetchall()[0][0],'completed')
  self.assertEqual(self.s.db.execute("SELECT state FROM global_source_partition WHERE run_id='stoppedcat' ORDER BY position").fetchall()[1][0],'stopped')
  self.assertEqual(self.s.stop_unpublished_category('stoppedcat')['state'],'stopped')
  self.assertIsNone(self.s.next_partition('stoppedcat'))
  with self.assertRaisesRegex(GlobalSourceError,'category_stop_scope_invalid'):
   self.s.stop_unpublished_category('one')
 def test_operator_stop_preserves_unpublished_duplicate_plain_pages(self):
  self.s.page('one',1,page([1],True,3))
  before=self.s.status('one')
  stopped=self.s.stop_unpublished_plain('one')
  self.assertEqual((stopped['state'],stopped['reason'],stopped['products'],stopped['pages']),
                   ('stopped','operator_stopped_duplicate_plain_collection',before['products'],before['pages']))
  self.assertFalse(stopped['published'])
  self.assertEqual(self.s.stop_unpublished_plain('one')['state'],'stopped')
  self.s.start('next_plain',self.scope)
  self.s.page('next_plain',1,page([2],False,1));self.s.finish_session('next_plain',True)
  with self.assertRaisesRegex(GlobalSourceError,'plain_stop_scope_invalid'):
   self.s.stop_unpublished_plain('next_plain')
 def test_zero_result_category_may_omit_products(self):
  self.s.blocked('one','fixture_end')
  self.s.start_partitioned('emptycat',self.scope,[{'category_id':'600099','name':'空类目','is_leaf':False}])
  self.s.next_partition('emptycat')
  self.s.partition_page('emptycat','600099',1,{'total':0,'has_more':False},
                        request_payload=list_request(1,category_id='600099'))
  self.s.finish_session('emptycat',True);result=self.s.status('emptycat')
  self.assertEqual((result['state'],result['products'],result['reportedTotal']),('completed',0,0))
  self.assertTrue(result['published'])
 def test_one_incomplete_category_never_replaces_the_published_head(self):
  self.s.page('one',1,page([9]));self.s.finish_session('one',True);old=self.s.status('one')['activePublished']['id']
  self.now+=10;self.s.start_partitioned('badcat',self.scope,[{'category_id':'600001','name':'家居用品','is_leaf':False}])
  self.s.next_partition('badcat')
  self.s.partition_page('badcat','600001',1,page([1],False,2),request_payload=list_request(1,category_id='600001'))
  self.s.finish_session('badcat',True);result=self.s.status('badcat')
  self.assertEqual(result['state'],'partial');self.assertFalse(result['published'])
  self.assertEqual(result['activePublished']['id'],old)
 def test_category_total_drift_completes_only_when_members_cover_the_final_total(self):
  self.s.blocked('one','fixture_end')
  self.s.start_partitioned('drift',self.scope,[{'category_id':'600001','name':'家纺布艺','is_leaf':False},
                                              {'category_id':'600002','name':'玩具','is_leaf':False}])
  self.s.next_partition('drift')
  # Two products are delisted between the pages: the total falls from 5 to 3 but 4 members are held.
  self.s.partition_page('drift','600001',1,page([1,2],True,5),request_payload=list_request(1,category_id='600001'))
  self.s.partition_page('drift','600001',2,page([3,4],False,3),request_payload=list_request(2,5,'600001'))
  first=self.s.db.execute("SELECT state,terminal_reason,unique_count,reported_total FROM global_source_partition WHERE run_id='drift' AND category_id='600001'").fetchone()
  self.assertEqual(tuple(first),('completed','endpoint_end_total_drift',4,3))
  self.assertEqual(self.s.next_partition('drift')['category_id'],'600002')
  # Drift that leaves fewer members than the final total still stops the run as inconsistent.
  self.s.partition_page('drift','600002',1,page([5],True,4),request_payload=list_request(1,category_id='600002'))
  self.s.partition_page('drift','600002',2,page([6],False,3),request_payload=list_request(2,4,'600002'))
  second=self.s.db.execute("SELECT state,terminal_reason FROM global_source_partition WHERE run_id='drift' AND category_id='600002'").fetchone()
  self.assertEqual(tuple(second),('partial','category_endpoint_total_mismatch'))
  self.assertEqual(self.s.status('drift')['state'],'partial')
 def test_partial_category_can_be_discarded_and_restarted_before_publication(self):
  self.s.blocked('one','fixture_end')
  self.s.start_partitioned('retrycat',self.scope,[{'category_id':'600001','name':'家居用品','is_leaf':False}])
  self.s.next_partition('retrycat')
  self.s.partition_page('retrycat','600001',1,page([1,2],True,3),request_payload=list_request(1,category_id='600001'))
  self.s.partition_page('retrycat','600001',2,page([2],False,3),request_payload=list_request(2,3,'600001'))
  self.assertEqual(self.s.status('retrycat')['state'],'partial')
  self.assertEqual(self.s.retry_partial_partition('retrycat'),'600001')
  state=self.s.status('retrycat');self.assertEqual((state['state'],state['products'],state['pages']),('collecting',0,0))
  self.assertEqual(self.s.next_partition('retrycat')['next_page'],1)
 def test_partial_category_repair_adds_only_missing_member_and_resumes(self):
  self.s.blocked('one','fixture_end')
  self.s.start_partitioned('repaircat',self.scope,[{'category_id':'600001','name':'家居用品','is_leaf':False}]);self.s.next_partition('repaircat')
  self.s.partition_page('repaircat','600001',1,page([1,2],True,3),request_payload=list_request(1,category_id='600001'))
  self.s.partition_page('repaircat','600001',2,page([2],False,3),request_payload=list_request(2,3,'600001'))
  scope=self.s.partial_repair_scope('repaircat');self.assertIn(2,scope['pages'])
  result=self.s.repair_partition_page('repaircat','600001',2,page([3],False,3),list_request(2,3,'600001'),scope['attempt'])
  self.assertTrue(result['complete']);self.assertEqual(self.s.status('repaircat')['state'],'completed')
  self.assertEqual(self.s.status('repaircat')['products'],3)
 def test_stable_duplicate_row_is_accepted_only_after_exact_replay(self):
  self.s.blocked('one','fixture_end')
  self.s.start_partitioned('stabledup',self.scope,[{'category_id':'600001','name':'家居用品','is_leaf':False}]);self.s.next_partition('stabledup')
  self.s.partition_page('stabledup','600001',1,page([1,2],True,3),request_payload=list_request(1,category_id='600001'))
  self.s.partition_page('stabledup','600001',2,page([2],False,3),request_payload=list_request(2,3,'600001'))
  scope=self.s.partial_repair_scope('stabledup')
  self.s.repair_partition_page('stabledup','600001',2,page([2],False,3),list_request(2,3,'600001'),scope['attempt'])
  accepted=self.s.accept_stable_duplicate_rows('stabledup');self.assertEqual(accepted['duplicateRows'],1)
  state=self.s.status('stabledup');self.assertEqual((state['state'],state['products'],state['reportedTotal'],state['stableDuplicateRows']),('completed',2,3,1))
 def test_verified_relogin_resumes_the_exact_blocked_category_page(self):
  self.s.blocked('one','fixture_end')
  self.s.start_partitioned('relogin',self.scope,[{'category_id':'600001','name':'家居用品','is_leaf':False}]);self.s.next_partition('relogin')
  self.s.blocked('relogin','source_remote_rejected');proof={'http':200,'code':16201010,'verification':False,'page':1,'categoryId':'600001'}
  with self.assertRaises(GlobalSourceError):self.s.resume_partition_after_relogin('relogin',proof,self.now)
  self.s.resume_partition_after_relogin('relogin',proof,self.now+1)
  self.assertEqual(self.s.status('relogin')['state'],'collecting')
 def test_operator_can_publish_a_frozen_partial_snapshot_without_calling_it_complete(self):
  self.s.blocked('one','fixture_end')
  categories=[{'category_id':'600001','name':'家居用品','is_leaf':False},
              {'category_id':'600002','name':'玩具','is_leaf':False}]
  self.s.start_partitioned('accepted',self.scope,categories);self.s.next_partition('accepted')
  self.s.partition_page('accepted','600001',1,page([1],False,1),request_payload=list_request(1,category_id='600001'))
  self.s.next_partition('accepted')
  self.s.partition_page('accepted','600002',1,page([2],True,20),request_payload=list_request(1,category_id='600002'))
  self.s.finish_session('accepted',True)
  acceptance=self.s.accept_partial_snapshot('accepted');status=self.s.status('accepted')
  self.assertEqual((acceptance['products'],acceptance['pages'],acceptance['categories_completed'],acceptance['category_count']),(2,2,1,2))
  self.assertEqual((status['state'],status['coverage'],status['reason']),('accepted_partial','operator_accepted_partial','operator_accepted_partial'))
  self.assertTrue(status['published']);self.assertEqual(status['activePublished']['id'],'accepted')
  self.assertEqual(status['categoriesCompleted'],1);self.assertEqual(status['categoryCount'],2)
  self.assertEqual(self.s.accept_partial_snapshot('accepted')['action'],'accept_partial_snapshot')
 def test_weekly_plain_refresh_overlays_only_existing_category_pids(self):
  self.s.blocked('one','fixture_end')
  categories=[{'category_id':'600001','name':'家居用品','is_leaf':False},
              {'category_id':'600002','name':'玩具','is_leaf':False}]
  self.s.start_partitioned('base',self.scope,categories);self.s.next_partition('base')
  self.s.partition_page('base','600001',1,page([1,2],False,2),request_payload=list_request(1,category_id='600001'))
  self.s.next_partition('base')
  self.s.partition_page('base','600002',1,page([3],True,2),request_payload=list_request(1,category_id='600002'))
  self.s.finish_session('base',True);self.s.accept_partial_snapshot('base')
  self.now+=10;self.s.start('weekly',self.scope)
  fresh=page([2,4]);fresh['products'][0]['title']='Updated product'
  self.s.page('weekly',1,fresh);self.s.finish_session('weekly',True)
  current=self.s.status();overlay=current['coverageOverlay']
  self.assertEqual((current['products'],current['state'],current['coverage']),(3,'accepted_partial','operator_accepted_partial'))
  self.assertEqual((overlay['baselineRunId'],overlay['overlapProducts'],overlay['refreshOutsideCoverage']),('base',1,1))
  self.assertEqual((current['categoryCount'],current['categoriesCompleted']),(2,1))
  products={row['pid']:row for row in self.s.products(limit=10)['items']}
  self.assertEqual(set(products),{product(n)['product_id'] for n in (1,2,3)})
  self.assertEqual(products[product(2)['product_id']]['title'],'Updated product')
  self.assertFalse(self.s.status('weekly')['published'])
  self.now+=10;self.s.start('weekly2',self.scope)
  self.s.page('weekly2',1,page([3,5]));self.s.finish_session('weekly2',True)
  second=self.s.status();self.assertEqual(second['products'],3)
  self.assertEqual(second['coverageOverlay']['baselineRunId'],'base')
  self.assertEqual(second['coverageOverlay']['refreshOutsideCoverage'],1)
  self.assertEqual(self.s.reconcile_category_coverage('it',apply=True)['duplicate'],True)
 def test_existing_plain_head_can_be_rebased_once_without_creating_products_outside_baseline(self):
  self.s.blocked('one','fixture_end')
  self.s.start_partitioned('base',self.scope,[{'category_id':'600001','name':'家居用品','is_leaf':False}]);self.s.next_partition('base')
  self.s.partition_page('base','600001',1,page([1,2],False,2),request_payload=list_request(1,category_id='600001'))
  self.s.finish_session('base',True)
  self.now+=10;self.s.start('weekly',self.scope);self.s.page('weekly',1,page([2,3]))
  # Reproduce a legacy publication where the bounded plain result replaced the category head.
  with self.s.tx():
   self.s.db.execute("UPDATE global_source_run SET identity_unchanged=1 WHERE id='weekly'")
   self.s.db.execute("UPDATE global_source_head SET run_id='weekly'")
  preview=self.s.reconcile_category_coverage('it')
  self.assertEqual((preview['products'],preview['overlapProducts'],preview['refreshOutsideCoverage']),(2,1,1))
  self.assertEqual(self.s.status()['id'],'weekly')
  applied=self.s.reconcile_category_coverage('it',apply=True)
  self.assertFalse(applied['duplicate']);self.assertEqual(self.s.status()['products'],2)
  self.assertEqual(self.s.reconcile_category_coverage('it',apply=True)['runId'],applied['runId'])
if __name__=='__main__':unittest.main()
