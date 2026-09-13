import sys,unittest,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_kalodata import KalodataWorker,parse_page,sales
from test_second_cycle import offer,NOW
class Provider:
 def __init__(self,rows=None):self.calls=0;self.rows=rows if rows is not None else [{'id':'kd1','handle':'someone','sale':'3','revenue':'15.50'}];self.payload=None
 def request(self,path,payload):self.calls+=1;self.payload=payload;return {'success':True,'data':self.rows}
class WorkerTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.path=Path(self.t.name)/'db.sqlite';self.now=NOW;self.s=CycleStore(self.path,lambda:self.now);self.p=self.s.plan('test','it')
  self.s.publish(self.p,'source',NOW,[offer()]);self.s.replenish(self.p,new_remaining=100,established_capacity=0,window_end='2026-09-13')
 def tearDown(self):self.s.close();self.t.cleanup()
 def test_positive_sales_and_no_fake_oec(self):
  p=Provider();r=KalodataWorker(self.s,p).once(self.p);self.assertEqual(r['status'],'completed');self.assertEqual(r['addedEdges'],1)
  e=json.loads(self.s.db.execute('SELECT payload FROM source_edge').fetchone()[0]);self.assertIsNone(e['oec']);self.assertIsNone(e['creatorId']);self.assertEqual(e['sourceHandle'],'someone');self.assertEqual(self.s.status(self.p)['relationships'],0)
  self.assertEqual(p.payload['startDate'],'2026-08-31');self.assertEqual(p.payload['endDate'],'2026-09-13')
 def test_sale_parsing(self):
  self.assertEqual(sales('1.2k'),1200);self.assertIsNone(sales(True));self.assertIsNone(sales('NaN'));self.assertIsNone(sales('0.5'))
 def test_replay_completed_does_not_request_again(self):
  p=Provider();w=KalodataWorker(self.s,p);w.once(self.p);self.assertEqual(w.once(self.p)['status'],'idle');self.assertEqual(p.calls,1)
 def test_receipt_survives_crash_before_commit(self):
  p=Provider();w=KalodataWorker(self.s,p);original=self.s.page
  self.s.page=lambda *a:(_ for _ in ()).throw(KeyboardInterrupt())
  with self.assertRaises(KeyboardInterrupt):w.once(self.p)
  self.s.page=original;self.now+=121;self.s.close();self.s=CycleStore(self.path,lambda:self.now)
  r=KalodataWorker(self.s,p,owner='new').once(self.p);self.assertEqual(r['status'],'completed');self.assertEqual(p.calls,1)
 def test_saved_receipt_does_not_expand_page_scope(self):
  p=Provider();w=KalodataWorker(self.s,p);original=self.s.page;self.s.page=lambda *a:(_ for _ in ()).throw(KeyboardInterrupt())
  with self.assertRaises(KeyboardInterrupt):w.once(self.p)
  self.s.page=original;self.now+=121
  self.assertEqual(KalodataWorker(self.s,p,max_pages=3).once(self.p)['error'],'kalodata_scope_changed');self.assertEqual(p.calls,1)
 def test_failure_blocks_job(self):
  p=Provider();p.request=lambda *a: {'success':False,'data':[]}
  r=KalodataWorker(self.s,p).once(self.p);self.assertEqual(r['status'],'blocked');self.assertEqual(self.s.status(self.p)['sourceEdges'],0)
 def test_empty_success_is_not_error(self):
  r=KalodataWorker(self.s,Provider([])).once(self.p);self.assertEqual(r['status'],'completed');self.assertEqual(r['addedEdges'],0)
 def test_pause_during_read_keeps_receipt_not_edges(self):
  p=Provider();read=p.request
  def call(*args):self.s.control(self.p,'pause',1,'paused');return read(*args)
  p.request=call;r=KalodataWorker(self.s,p).once(self.p);self.assertEqual(r['error'],'plan_paused');self.assertEqual(self.s.status(self.p)['sourceEdges'],0);self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_source_receipt').fetchone()[0],1)
 def test_lease_loss_does_not_save_or_import(self):
  p=Provider();read=p.request
  def call(*args):self.now+=121;return read(*args)
  p.request=call;r=KalodataWorker(self.s,p).once(self.p);self.assertEqual(r['status'],'blocked');self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_source_receipt').fetchone()[0],0)
 def test_repeated_page_detected(self):
  p=Provider([{'id':str(i),'handle':'user'+str(i),'sale':1} for i in range(50)]);w=KalodataWorker(self.s,p)
  self.assertEqual(w.once(self.p)['status'],'checkpointed');self.assertEqual(w.once(self.p)['error'],'kalodata_repeated_page')
 def test_pending_identity_backpressure(self):
  p=Provider([{'id':str(i),'handle':'user'+str(i),'sale':1} for i in range(50)])
  self.s.replenish(self.p,new_remaining=10,established_capacity=0,window_end='2026-09-13')
  w=KalodataWorker(self.s,p);w.once(self.p);self.assertEqual(w.once(self.p)['status'],'idle');self.assertEqual(p.calls,1)
 def test_malformed_success_not_empty(self):
  p=Provider();p.request=lambda *a:{'success':True,'data':{}}
  self.assertEqual(KalodataWorker(self.s,p).once(self.p)['error'],'kalodata_rows_invalid')
 def test_cap_is_labelled_not_complete_universe(self):
  p=Provider([{'id':str(i),'handle':'user'+str(i),'sale':1} for i in range(50)])
  r=KalodataWorker(self.s,p,max_pages=1).once(self.p);self.assertEqual(r['status'],'completed');self.assertEqual(r['coverage'],'page_cap')
if __name__=='__main__':unittest.main()
