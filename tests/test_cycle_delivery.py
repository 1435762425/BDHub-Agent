import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib.cycle_delivery import Deliveries
from test_second_cycle import offer,edge,NOW
class DeliveryTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.now=NOW;self.s=CycleStore(Path(self.t.name)/'db',lambda:self.now);self.p=self.s.plan('test','it');self.s.publish(self.p,'s',NOW,[offer()]);self.s.import_edges(self.p,[edge()]);r=self.s.db.execute('SELECT * FROM relationship').fetchone()
  self.c={'creatorId':r['creator_id'],'oecId':r['oec'],'pid':offer()['pid'],'source':{'sourceId':edge()['sourceId']},'offer':offer(),'planRevision':1,'controlRevision':1,'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'Hello'}};self.d=Deliveries(self.s);self.id=self.d.prepare(self.p,self.c)['id']
 def tearDown(self):self.s.close();self.t.cleanup()
 def begin(self,kind):return self.d.begin(self.id,kind,authorized_snapshot_hash=digest(self.c),recipient_verified=True,allowance_verified=True)
 def proof(self,kind):return {'status':'confirmed','requestRef':next(p['request_ref'] for p in self.d.get(self.id)['parts'] if p['kind']==kind),'oecId':self.c['oecId'],'messageId':'10','evidenceRef':'verified','kind':kind}
 def test_order_receipt_is_not_delivery_and_finished_not_repeated(self):
  with self.assertRaisesRegex(CycleError,'card_not_confirmed'):self.begin('text')
  ref=self.begin('card')['requestRef'];self.d.receipt(self.id,'card',{'requestRef':ref})
  with self.assertRaisesRegex(CycleError,'verify_before_dispatch'):self.begin('text')
  self.d.confirm(self.id,'card',self.proof('card'));self.begin('text');self.d.confirm(self.id,'text',self.proof('text'))
  self.assertEqual(self.d.get(self.id)['state'],'confirmed');self.assertEqual(self.d.prepare(self.p,self.c)['id'],self.id)
  with self.assertRaises(CycleError):self.begin('card')
 def test_crash_restarts_as_verification_not_resend(self):
  self.begin('card');self.d=Deliveries(self.s)
  with self.assertRaises(CycleError):self.begin('card')
  self.d.unknown(self.id,'card');self.d.confirm(self.id,'card',self.proof('card'));self.begin('text')
 def test_pause_between_card_and_text(self):
  self.begin('card');self.d.confirm(self.id,'card',self.proof('card'));self.s.control(self.p,'pause',1,'paused')
  with self.assertRaisesRegex(CycleError,'plan_changed'):self.begin('text')
 def test_reply_between_components_preserves_card(self):
  self.begin('card');self.d.confirm(self.id,'card',self.proof('card'));self.s.inbound(self.p,self.c['creatorId'],'reply')
  with self.assertRaisesRegex(CycleError,'relationship_changed'):self.begin('text')
  self.assertEqual(self.d.get(self.id)['parts'][0]['state'],'confirmed')
 def test_frozen_scope_and_expiry(self):
  with self.assertRaises(CycleError):self.d.prepare(self.p,self.c|{'message':{'textIt':'different'}})
  self.now+=1801
  with self.assertRaisesRegex(CycleError,'delivery_expired'):self.begin('card')
 def test_missing_quota_evidence_no_dispatch(self):
  with self.assertRaisesRegex(CycleError,'execution_evidence_missing'):self.d.begin(self.id,'card',authorized_snapshot_hash=digest(self.c),recipient_verified=True)
 def test_reserved_source_exits_ready_supply_without_deleting_history(self):
  self.assertEqual(self.s._eligible_people(self.p),set())
  self.assertGreater(self.s.db.execute('SELECT count(*) FROM source_edge').fetchone()[0],0)
 def test_wrong_recipient_receipt_cannot_confirm(self):
  self.begin('card')
  with self.assertRaises(CycleError):self.d.confirm(self.id,'card',self.proof('card')|{'oecId':'other'})
if __name__=='__main__':unittest.main()
