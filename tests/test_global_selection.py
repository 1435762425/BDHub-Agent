import sqlite3
from contextlib import closing
import sys
import tempfile,unittest,tempfile,json,sqlite3
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
        with closing(sqlite3.connect(root / 'var/global-source.sqlite')) as conn,conn:
            conn.executescript('CREATE TABLE global_source_head(scope_hash TEXT,run_id TEXT);'
                               'CREATE TABLE global_source_run(id TEXT,scope TEXT);'
                               'CREATE TABLE global_source_product(run_id TEXT,pid TEXT,payload TEXT);')
            conn.execute("INSERT INTO global_source_run VALUES('r1',?)",
                         (json.dumps({'market': 'it', 'account': 'acc6'}),))
            conn.execute("INSERT INTO global_source_head VALUES('h','r1')")
            for pid in pids:
                conn.execute('INSERT INTO global_source_product VALUES(?,?,?)',
                             ('r1', pid, json.dumps({'product_id': pid, 'sales': '900 已售',
                                                     'product_rating': 4.5, 'commission_rate': 1400,
                                                     'open_collab_rate': 900, 'fs_is_selected': False})))
            conn.commit()
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
                self.assertEqual([item['pid'] for item in ledger.items(second)], ['2' * 19])
            finally:
                ledger.db.close()

    def test_a_merely_filtered_product_is_still_eligible_for_a_later_batch(self):
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
                self.assertEqual([item['pid'] for item in ledger.items(second)], ['3' * 19])
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
                self.assertEqual(copied[0]['payload']['recoveredFromRun'],first)
                self.assertEqual(copied[0]['payload']['receipt'],{'http':200,'code':0})
            finally:ledger.db.close()
