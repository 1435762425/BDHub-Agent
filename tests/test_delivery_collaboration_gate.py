import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.collaboration_status import set_manual
from lib.cycle_delivery import Deliveries
from lib.market_send_canary import _settle_local_refusal,_settle_create_refusal
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore,digest
from test_second_cycle import NOW,offer,edge


class DeliveryCollaborationGateTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  with CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW) as store:self.plan=store.plan('bjn-local-research','it')
  apply_database(self.root,'second-cycle',clock=lambda:NOW)
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW)
  self.store.publish(self.plan,'source',NOW,[offer()]);self.store.import_edges(self.plan,[edge()])
  relation=self.store.db.execute('SELECT * FROM relationship WHERE plan_id=?',(self.plan,)).fetchone()
  self.candidate={'creatorId':relation['creator_id'],'oecId':relation['oec'],'pid':offer()['pid'],
   'source':{'sourceId':edge()['sourceId']},'offer':offer(),'planRevision':1,'controlRevision':relation['revision'],
   'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'Hello'}}
  self.deliveries=Deliveries(self.store)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def change(self,status):
  creator=self.candidate['creatorId']
  control=self.store.db.execute('SELECT revision FROM relationship WHERE plan_id=? AND creator_id=?',(self.plan,creator)).fetchone()[0]
  current=self.store.db.execute('SELECT revision FROM creator_collaboration_current WHERE plan_id=? AND creator_id=?',(self.plan,creator)).fetchone()
  return set_manual(self.store,creator,status,'manual-collaboration-'+status,current[0] if current else 0,control,plan_id=self.plan)
 def refresh_candidate(self):
  self.candidate['controlRevision']=self.store.db.execute('SELECT revision FROM relationship WHERE plan_id=? AND creator_id=?',
   (self.plan,self.candidate['creatorId'])).fetchone()[0]
 def begin(self,did,kind):
  return self.deliveries.begin(did,kind,authorized_snapshot_hash=digest(self.candidate),recipient_verified=True,allowance_verified=True)
 def test_paid_between_pool_and_relation_reads_blocks_prepare_with_the_latest_revision(self):
  self.change('paid');self.refresh_candidate()
  relation=self.store.db.execute('SELECT mode,rejected FROM relationship WHERE plan_id=?',(self.plan,)).fetchone()
  self.assertEqual(tuple(relation),('auto',0))
  with self.assertRaisesRegex(CycleError,'collaboration_excluded'):self.deliveries.prepare(self.plan,self.candidate)
  self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_delivery').fetchone()[0],0)
 def test_rejected_projection_is_also_checked_independently_of_relationship_flag(self):
  self.change('rejected');self.refresh_candidate()
  self.store.db.execute('UPDATE relationship SET rejected=0 WHERE plan_id=?',(self.plan,))
  with self.assertRaisesRegex(CycleError,'collaboration_excluded'):self.deliveries.prepare(self.plan,self.candidate)
 def test_paid_after_prepare_blocks_conversation_and_card_without_stopping_the_market(self):
  did=self.deliveries.prepare(self.plan,self.candidate)['id'];self.deliveries.prepare_conversation(did)
  self.change('paid')
  with self.assertRaisesRegex(CycleError,'collaboration_excluded'):self.deliveries.begin_conversation(did,digest(self.candidate))
  with self.assertRaisesRegex(CycleError,'collaboration_excluded'):self.begin(did,'card')
  self.assertEqual(self.deliveries.conversation_intent(did)['state'],'ready')
  self.assertEqual([p['state'] for p in self.deliveries.get(did)['parts']],['ready','ready'])
  # A recipient-specific local refusal cancels only this unsubmitted invitation.
  from lib.italy_im_delivery import ItalyImDeliveryError
  try:
   try:self.deliveries.begin_conversation(did,digest(self.candidate))
   except CycleError:raise ItalyImDeliveryError('it_delivery_dispatch_not_allowed') from None
  except ItalyImDeliveryError as error:result=_settle_create_refusal(self.store,did,error)
  self.assertEqual(result['state'],'cancelled')
  self.assertEqual(self.store._plan(self.plan)['state'],'active')
 def test_paid_between_card_and_text_preserves_confirmed_card_and_cancels_unsent_text(self):
  did=self.deliveries.prepare(self.plan,self.candidate)['id'];permit=self.begin(did,'card')
  self.deliveries.confirm(did,'card',{'status':'confirmed','requestRef':permit['requestRef'],'oecId':self.candidate['oecId'],
   'kind':'card','messageId':'1','evidenceRef':'verified-card'})
  self.change('paid')
  with self.assertRaisesRegex(CycleError,'collaboration_excluded'):self.begin(did,'text')
  result=_settle_local_refusal(self.store,did,'text',CycleError('collaboration_excluded'))
  self.assertEqual(result['state'],'partial_delivery')
  self.assertEqual([p['state'] for p in self.deliveries.get(did)['parts']],['confirmed','cancelled'])
 def test_submitted_conversation_is_never_cancelled_by_a_later_paid_change(self):
  from lib.italy_im_delivery import ItalyImDeliveryError
  did=self.deliveries.prepare(self.plan,self.candidate)['id'];self.deliveries.prepare_conversation(did)
  self.deliveries.begin_conversation(did,digest(self.candidate));self.deliveries.unknown(did,'card')
  self.change('paid')
  try:
   try:raise CycleError('collaboration_excluded')
   except CycleError:raise ItalyImDeliveryError('it_delivery_dispatch_not_allowed') from None
  except ItalyImDeliveryError as error:self.assertIsNone(_settle_create_refusal(self.store,did,error))
  self.assertEqual(self.deliveries.get(did)['state'],'unknown')
  self.assertEqual(self.deliveries.conversation_intent(did)['state'],'inflight')
 def test_manual_normal_and_collaborated_remain_eligible_after_latest_revision(self):
  for status in ('normal','collaborated'):
   with self.subTest(status=status):
    self.change(status);self.refresh_candidate()
    self.candidate['source']={'sourceId':'source-'+status}
    did=self.deliveries.prepare(self.plan,self.candidate)['id']
    self.deliveries.cancel_unsubmitted(did,'test_complete')
 def test_paid_in_another_market_does_not_block_this_plan(self):
  other=self.store.plan('bjn-local-research','my')
  self.store.db.execute("INSERT INTO creator_collaboration_current VALUES(?,'my',?,'paid','manual',1,?)",
                       (other,self.candidate['creatorId'],NOW))
  did=self.deliveries.prepare(self.plan,self.candidate)['id']
  self.assertTrue(self.begin(did,'card')['dispatchAllowed'])


if __name__=='__main__':unittest.main()
