import sqlite3
from contextlib import closing
import sys
import tempfile,unittest,tempfile,json,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.global_selection import sales,assess,choose_campaign,matching_selection_evidence,prioritized_selection_batch,selection_campaign,Selection,observations,selected_rows,retryable_auth_rejection,retryable_verification_rejection,settle_readback
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
 def test_native_listing_batch_uses_the_frozen_campaign_without_detail_reads(self):
  class Transport:
   def offers(self,_pid):raise AssertionError('native batch must not read one offer at a time')
  product={'product_id':'1'*19,'campaign_id':'7'*19}
  result=selection_campaign(Transport(),product,1,native_listing=True)
  self.assertEqual(result['campaign']['campaign_id'],'7'*19)
  self.assertEqual(result['selectionSource'],'global_listing')
 def test_retried_items_are_not_duplicated_when_filling_the_batch(self):
  retried=[{'pid':'1','state':'pending'}]
  items=[{'pid':'1','state':'pending'},{'pid':'2','state':'pending'}]
  self.assertEqual([row['pid'] for row in prioritized_selection_batch(items,retried,2)],['1','2'])
 def test_submitted_intent_only_accepts_its_frozen_campaign(self):
  observed=[{'pid':'p','campaignId':'other','type':1,'totalBasis':'1300'},{'pid':'p','campaignId':'original','type':8,'totalBasis':'1300'}]
  item={'pid':'p','state':'result_unknown','payload':{'campaign':{'campaign':{'campaign_id':'original'},'freshProduct':{'commission_rate':'1300'}}}}
  self.assertEqual(matching_selection_evidence(item,observed),[observed[1]])
  self.assertEqual(matching_selection_evidence(item,observed[:1]),[])
  self.assertEqual(matching_selection_evidence({'pid':'p','state':'pending','payload':{}},observed),observed)
 def test_the_frozen_campaign_also_needs_the_submitted_total_commission(self):
  item={'pid':'p','state':'result_unknown','payload':{'campaign':{'campaign':{'campaign_id':'original'},'freshProduct':{'commission_rate':'1500'}}}}
  same={'pid':'p','campaignId':'original','type':9}
  for basis in ('1200',None,'x','NaN','Infinity'):
   self.assertEqual(matching_selection_evidence(item,[{**same,'totalBasis':basis}]),[],basis)
  self.assertEqual(matching_selection_evidence(item,[{**same,'totalBasis':'1500'}]),[{**same,'totalBasis':'1500'}])
  unrecorded={'pid':'p','state':'result_unknown','payload':{'campaign':{'campaign':{'campaign_id':'original'}}}}
  self.assertEqual(matching_selection_evidence(unrecorded,[{**same,'totalBasis':'1500'}]),[])
 def test_platform_assigned_campaign_settles_only_at_the_submitted_total_commission(self):
  item={'pid':'p','state':'result_unknown','payload':{'campaign':{'campaign':{'campaign_id':'listing'},'freshProduct':{'commission_rate':'1300'}}}}
  rehomed={'pid':'p','campaignId':'assigned','type':9,'totalBasis':'1300'}
  self.assertEqual(matching_selection_evidence(item,[rehomed]),[{**rehomed,'platformAssignedCampaign':True}])
  # Another total, a non full-managed campaign, or an intent without a recorded total never settle.
  self.assertEqual(matching_selection_evidence(item,[{**rehomed,'totalBasis':'1200'}]),[])
  self.assertEqual(matching_selection_evidence(item,[{**rehomed,'type':1}]),[])
  self.assertEqual(matching_selection_evidence(item,[{**rehomed,'totalBasis':None}]),[])
  bare={'pid':'p','state':'result_unknown','payload':{'campaign':{'campaign':{'campaign_id':'listing'}}}}
  self.assertEqual(matching_selection_evidence(bare,[rehomed]),[])
  # The detail route records the total on the campaign itself.
  detail={'pid':'p','state':'awaiting_verification','payload':{'campaign':{'campaign':{'campaign_id':'x','commission':'1300'}}}}
  self.assertEqual(len(matching_selection_evidence(detail,[rehomed])),1)
 def test_readback_row_carries_the_total_commission(self):
  from lib.global_selection import readback_row
  # Shape observed live on 2026-09-28: basis points on the product row, no commission on the campaign.
  row=readback_row({'campaign_product':{'product_id':'1'*19,'total_commission_percent':'1500','plan_commission_percent':'900'},'campaign_info':{'campaign_id':'9'*19,'crs_campaign_type':9,'commission':None}})
  self.assertEqual(row,{'pid':'1'*19,'campaignId':'9'*19,'type':9,'totalBasis':'1500'})
  self.assertEqual(readback_row({'campaign_product':{'product_id':'1','partner_commission_percent':1100},'campaign_info':{'campaign_id':'2'}})['totalBasis'],'1100')
 def test_a_refresh_or_relogin_after_the_attempt_proves_the_login_again(self):
  from lib.global_selection import latest_relogin_after
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();(root/'config').mkdir()
   with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db,db:
    db.execute('CREATE TABLE account_identity_generation(market,account,state,reason,published_at)')
    db.executemany('INSERT INTO account_identity_generation VALUES(?,?,?,?,?)',
                   [('uk','acc4','published','capability',300),('uk','acc11','published','relogin',300),('uk','acc4','published','refresh',50)])
   class Ledger:pass
   ledger=Ledger();ledger.root=root;ledger.market='uk';item={'payload':{'attemptedAt':100}}
   from unittest.mock import patch
   with patch('lib.market_accounts.load_config',return_value={'markets':{'uk':{'roles':{'supply':'acc4','communications':'acc11'}}}}):
    # Neither a capability check, another account's relogin nor an earlier refresh counts.
    self.assertIsNone(latest_relogin_after(ledger,item))
    with closing(sqlite3.connect(root/'var/second-cycle.sqlite')) as db,db:
     db.execute("INSERT INTO account_identity_generation VALUES('uk','acc4','published','refresh',200)")
    self.assertEqual(latest_relogin_after(ledger,item),200)
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
 def test_explicit_auth_rejection_can_continue_only_after_a_new_relogin(self):
  r={'http':200,'code':16201010,'verification':False,'ambiguous':False,'systemError':False}
  i={'pid':'p','state':'result_unknown','payload':{'receipt':r,'attemptedAt':10}}
  fresh={'product_id':'p','fs_is_selected':False,'sales':'300 已售','product_rating':4,'commission_rate':'1200','open_collab_rate':'1000'}
  self.assertTrue(retryable_auth_rejection(i,set(),fresh,11))
  self.assertFalse(retryable_auth_rejection(i,set(),fresh,None))
  self.assertFalse(retryable_auth_rejection(i,{'p'},fresh,11))
  self.assertFalse(retryable_auth_rejection(i|{'payload':{'receipt':r|{'ambiguous':True}}},set(),fresh,11))
 def test_ambiguous_write_is_skipped_not_retried_after_two_delayed_absences(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root)
   try:
    with ledger.db:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','result_unknown',json.dumps({'receipt':{'http':0,'ambiguous':True}}),1))
    item=ledger.items('r')[0];ledger.record_readback_absence(item,at=10)
    self.assertEqual(ledger.skip_unknown('r',at=20),[])
    ledger.record_readback_absence(item,at=40)
    self.assertEqual(ledger.skip_unknown('r',at=41),['p'])
    self.assertEqual(ledger.items('r')[0]['state'],'skipped_unknown')
   finally:ledger.db.close()
 def test_code_zero_without_readback_is_also_skipped_after_two_delayed_absences(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root)
   try:
    payload={'receipt':{'http':200,'code':0,'verification':False,'ambiguous':False}}
    with ledger.db:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','awaiting_verification',json.dumps(payload),1))
    item=ledger.items('r')[0];ledger.record_readback_absence(item,at=10);ledger.record_readback_absence(item,at=40)
    self.assertEqual(ledger.skip_unknown('r',at=41),['p'])
   finally:ledger.db.close()
 def test_other_campaign_reads_isolate_only_after_two_delayed_readbacks(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root)
   try:
    with ledger.db:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','submitting',json.dumps({'campaign':{'campaign':{'campaign_id':'7'*19}}}),1))
    item=ledger.items('r')[0];observed=[{'pid':'p','campaignId':'other','type':8}]
    self.assertFalse(ledger.record_other_campaign(item,observed,at=100))
    self.assertEqual(item['state'],'submitting');self.assertEqual(item['payload']['otherCampaignReads'][0]['campaignIds'],['other'])
    self.assertFalse(ledger.record_other_campaign(item,observed,at=399))
    self.assertEqual(item['state'],'submitting')
    self.assertTrue(ledger.record_other_campaign(item,observed,at=400))
    self.assertEqual(item['state'],'isolated_unverified')
    self.assertEqual(item['payload']['isolation']['reason'],'selection_campaign_mismatch')
    self.assertEqual(item['payload']['otherCampaignObserved'],observed)
   finally:ledger.db.close()
 def test_other_campaign_reads_ignore_items_that_were_never_submitted(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root)
   try:
    with ledger.db:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','pending','{}',1))
    item=ledger.items('r')[0]
    self.assertFalse(ledger.record_other_campaign(item,[{'pid':'p','campaignId':'other','type':8}],at=100))
    self.assertEqual(item['state'],'pending');self.assertNotIn('otherCampaignReads',item['payload'])
   finally:ledger.db.close()
 def test_settle_readback_confirms_only_the_frozen_campaign(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root)
   try:
    with ledger.db:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','result_unknown',json.dumps({'campaign':{'campaign':{'campaign_id':'7'*19,'commission':'1200'}}}),1))
    item=ledger.items('r')[0]
    self.assertEqual(settle_readback(ledger,item,[{'pid':'p','campaignId':'other','type':8,'totalBasis':'1100'}]),'other_campaign')
    self.assertEqual(item['state'],'result_unknown')
    self.assertEqual(settle_readback(ledger,item,[{'pid':'p','campaignId':'7'*19,'type':8,'totalBasis':'1200'}]),'settled')
    self.assertEqual(item['state'],'confirmed')
   finally:ledger.db.close()
 def test_settle_readback_marks_a_pending_item_seen_under_any_campaign(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root)
   try:
    with ledger.db:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','pending','{}',1))
    item=ledger.items('r')[0]
    self.assertEqual(settle_readback(ledger,item,[{'pid':'p','campaignId':'any','type':8}]),'settled')
    self.assertEqual(item['state'],'already_selected')
   finally:ledger.db.close()
 def test_settle_readback_records_absence_for_a_submitted_item(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root)
   try:
    with ledger.db:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','submitting','{}',1))
    item=ledger.items('r')[0]
    self.assertEqual(settle_readback(ledger,item,[],at=10),'absent')
    self.assertEqual(item['state'],'submitting');self.assertEqual(item['payload']['readbackAbsences'][0]['at'],10)
   finally:ledger.db.close()
 def test_verification_rejection_is_skipped_after_two_proven_retries(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);(root/'var').mkdir();ledger=Selection(root)
   try:
    payload={'receipt':{'http':200,'code':10000,'verification':True,'ambiguous':False},
             'platformVerification':'passed','priorAttempts':[{},{}],
             'readbackAbsences':[{'at':10,'selectedPoolAbsent':True},{'at':40,'selectedPoolAbsent':True}]}
    with ledger.db:ledger.db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',('r','p','result_unknown',json.dumps(payload),1))
    self.assertEqual(ledger.skip_unknown('r',at=41),['p'])
   finally:ledger.db.close()
if __name__=='__main__':unittest.main()


class PrepareSkipsSettled(unittest.TestCase):
    """A product already proven to be in the pool must not be enqueued into a later batch.

    A threshold change produces a different batch id, which is exactly when the old behaviour
    re-enqueued every eligible product -- including ones already confirmed to be in the pool, each
    of which then had to be read back just to be told so.
    """

    def _root(self, folder, pids):
        root = Path(folder)
        (root / 'config').mkdir(parents=True, exist_ok=True)
        (root / 'var').mkdir(parents=True, exist_ok=True)
        from lib.global_source import SCHEMA,SOURCE,FILTER
        with closing(sqlite3.connect(root / 'var/global-source.sqlite')) as conn,conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO global_source_run(id,scope,scope_hash,state,created,updated,identity_unchanged) VALUES('r1',?,'h','completed',1,2,1)",
                         (json.dumps({'market':'it','account':'acc6','source':SOURCE,'filter':FILTER}),))
            conn.execute("INSERT INTO global_source_head VALUES('h','r1')")
            for pid in pids:
                product={'product_id':pid,'sales':'900 已售','product_rating':4.5,'commission_rate':1400,
                         'open_collab_rate':900,'fs_is_selected':False}
                conn.execute('INSERT INTO global_source_product VALUES(?,?,?,?,?,?,?)',
                             ('r1',pid,json.dumps(product),'fingerprint',1,1,2))
        return root

    def _threshold(self, root, min_sales):
        (root / 'config/catalog-screen.json').write_text(json.dumps(
            {'version': 'catalog-screen-v1', 'minSales': min_sales, 'minRating': 4.0,
             'minCommissionGapPoints': 2.0, 'allowUnrated': True}), encoding='utf-8')

    def test_a_settled_product_is_not_enqueued_again_after_a_threshold_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self._root(folder, ['1' * 19, '2' * 19])
            self._threshold(root, 300)
            ledger = Selection(folder)
            try:
                first = ledger.prepare()
                self.assertEqual(len(ledger.items(first)), 2)
                ledger.db.execute("UPDATE intake_item SET state='already_selected' WHERE run_id=? AND pid=?",
                                  (first, '1' * 19))
                ledger.db.commit()
                self._threshold(root, 200)
                second = ledger.prepare()
                self.assertNotEqual(first, second)
                self.assertEqual([item['pid'] for item in ledger.items(second) if item['state']=='pending'], ['2' * 19])
                self.assertEqual(ledger.db.execute('SELECT count(*) FROM intake_item').fetchone()[0],2)
            finally:
                ledger.db.close()

    def test_a_filtered_product_is_not_reset_by_a_later_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self._root(folder, ['3' * 19])
            self._threshold(root, 300)
            ledger = Selection(folder)
            try:
                first = ledger.prepare()
                # Filtered means "did not clear the thresholds", not "proven to be in the pool".
                ledger.db.execute("UPDATE intake_item SET state='filtered' WHERE run_id=?", (first,))
                ledger.db.commit()
                self._threshold(root, 200)
                second = ledger.prepare()
                self.assertEqual([item['state'] for item in ledger.items(second)], ['filtered'])
            finally:
                ledger.db.close()

    def test_an_unknown_write_is_carried_forward_for_readback_not_resubmission(self):
        with tempfile.TemporaryDirectory() as folder:
            pid='4'*19;root=self._root(folder,[pid]);self._threshold(root,300)
            ledger=Selection(folder)
            try:
                first=ledger.prepare();item=ledger.items(first)[0]
                ledger.update(item,'result_unknown',receipt={'http':200,'code':0},campaign={'campaign':{'campaign_id':'7'*19}})
                self._threshold(root,200);second=ledger.prepare();copied=ledger.items(second)
                self.assertEqual(len(copied),1);self.assertEqual(copied[0]['state'],'result_unknown')
                self.assertEqual(copied[0]['run_id'],first)
                self.assertEqual(ledger.db.execute('SELECT count(*) FROM intake_item').fetchone()[0],1)
                self.assertEqual(copied[0]['payload']['receipt'],{'http':200,'code':0})
            finally:ledger.db.close()
