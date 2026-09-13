import sys,tempfile,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_delivery import Deliveries
from lib.cycle_reply_facts import ReplyFacts
from test_cycle_delivery import DeliveryTests
class FactTests(unittest.TestCase):
 _base=DeliveryTests.setUp
 tearDown=DeliveryTests.tearDown
 def setUp(self):
  self._base();self.c['name']={'shortNameIt':'cuscino'};self.c['card']={'listId':'789'};self.s.db.execute('DELETE FROM cycle_delivery_part');self.s.db.execute('DELETE FROM cycle_delivery');self.id=self.d.prepare(self.p,self.c)['id'];self.s.db.execute("UPDATE cycle_delivery SET state='confirmed' WHERE id=?",(self.id,));self.f=ReplyFacts(self.s,lambda c:{'state':'verified_read_only','pid':c['pid'],'listId':'789','creatorPercent':'13','checkedAt':self.now,'evidenceRefs':['proof'],'totalPercent':'99','publicPercent':'1'})
 def pending(self):
  from lib.cycle_inbox import Inbox
  from lib.cycle_service import Service
  Inbox(self.s);Service(self.s)
  self.s.db.execute("INSERT INTO inbox_pending VALUES(?,?,1,?,'needs_facts')",(self.p,self.c['creatorId'],self.now))
  self.s.db.execute('INSERT INTO service_assessment VALUES(?,?,?,?,?,?,?)',(self.p,self.c['creatorId'],1,'hash','[]',json.dumps({'requiredTools':['get_current_creator_commission']}),self.now))
 def test_facts_progress_without_automatic_reply(self):
  self.pending();r=self.f.process_due(self.p);self.assertEqual(r[0]['state'],'facts_ready_for_review');self.assertFalse(r[0]['automaticReply'])
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM service_fact_result').fetchone()[0],1)
 def test_new_message_during_network_read_rejects_old_fact(self):
  self.pending();original=self.f.refresh
  def changed(c):
   self.s.db.execute('UPDATE inbox_pending SET revision=2');return original(c)
  self.f.refresh=changed
  with self.assertRaisesRegex(CycleError,'context_changed'):self.f.process_due(self.p)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM service_fact_result').fetchone()[0],0)
 def test_only_creator_commission_exported(self):
  r=self.f.call('get_current_creator_commission',self.p,self.c['creatorId']);self.assertEqual(r['creatorPercent'],'13');self.assertNotIn('totalPercent',r);self.assertNotIn('publicPercent',r)
 def test_unknown_write_tool_never_runs(self):
  with self.assertRaisesRegex(CycleError,'not_allowed'):self.f.call('approve_sample',self.p,self.c['creatorId'])
 def test_no_confirmed_product_no_guess(self):
  self.s.db.execute("UPDATE cycle_delivery SET state='unknown'")
  with self.assertRaisesRegex(CycleError,'no_confirmed'):self.f.call('get_current_creator_commission',self.p,self.c['creatorId'])
 def test_wrong_card_identity_is_not_fact(self):
  self.f.refresh=lambda c:{'state':'verified_read_only','pid':'bad','listId':'789'}
  with self.assertRaisesRegex(CycleError,'binding_mismatch'):self.f.call('get_current_creator_commission',self.p,self.c['creatorId'])
del DeliveryTests
if __name__=='__main__':unittest.main()
