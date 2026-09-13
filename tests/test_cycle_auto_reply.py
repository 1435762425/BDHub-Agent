import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_cycle_service import ServiceTests
from lib.cycle_auto_reply import AutoReplies,ACK
from lib.cycle_delivery import Deliveries
from lib.second_cycle import CycleError
class AutoReplyTests(unittest.TestCase):
 _base_setup=ServiceTests._base_setup
 _service_setup=ServiceTests.setUp
 tearDown=ServiceTests.tearDown
 event=ServiceTests.event;ingest=ServiceTests.ingest;rel=ServiceTests.rel;content=ServiceTests.content;add=ServiceTests.add;baseline=ServiceTests.baseline;process=ServiceTests.process
 def setUp(self):self._service_setup();Deliveries(self.s);self.auto=AutoReplies(self.s)
 def enable(self):self.auto.enable(self.p,'explicit user automatic reply request')
 def handoff(self):self.enable();self.baseline();self.add('1','Il link non funziona');self.process();return self.auto.prepare(self.p,self.creator)
 def proof(self,q):return {'status':'confirmed','conversationId':'10','requestRef':q['request_ref'],'messageId':'12','evidenceRef':'exact-native-readback'}
 def test_disabled_never_creates_reply(self):
  self.baseline();self.add('1','Il link non funziona');self.process();self.assertIsNone(self.auto.prepare(self.p,self.creator))
 def test_handoff_ack_once_and_case_keeps_human_control(self):
  q=self.handoff();self.assertEqual(q['text'],ACK);self.auto.begin(q['id']);self.auto.confirm(q['id'],self.proof(q));self.assertEqual(self.rel()['mode'],'human');self.assertIsNone(self.auto.prepare(self.p,self.creator))
 def test_unknown_is_not_resent(self):
  q=self.handoff();self.auto.begin(q['id']);self.auto.unknown(q['id'])
  with self.assertRaises(CycleError):self.auto.begin(q['id'])
  self.auto.confirm(q['id'],self.proof(q));self.assertEqual(self.auto.get(q['id'])['state'],'confirmed')
 def test_new_message_invalidates_old_reply(self):
  q=self.handoff();self.now+=1;self.add('2','Anche il catalogo')
  with self.assertRaisesRegex(CycleError,'context_changed'):self.auto.begin(q['id'])
 def test_refusal_does_not_receive_ack(self):
  self.enable();self.baseline();self.add('1','Non mi contattare più');self.process();self.assertIsNone(self.auto.prepare(self.p,self.creator))
 def test_commission_needs_fresh_fact_and_exports_only_creator_rate(self):
  self.enable();self.baseline();self.add('1','Quanto è la commissione?');self.process();self.assertIsNone(self.auto.prepare(self.p,self.creator));q=self.auto.prepare(self.p,self.creator,{'creatorPercent':'13','observedAt':self.now,'totalPercent':'99'});self.assertIn('13%',q['text']);self.assertNotIn('99',q['text'])
 def test_new_service_question_after_opt_out_does_not_restore_marketing(self):
  self.enable();self.baseline();self.add('1','Non mi contattare più');self.process();self.now+=10;self.add('2','Quanto è la commissione?');self.process();q=self.auto.prepare(self.p,self.creator,{'creatorPercent':'13','observedAt':self.now});self.assertIsNotNone(q);self.assertTrue(self.rel()['rejected'])
 def test_operator_control_change_blocks_queued_reply(self):
  q=self.handoff();self.s.db.execute('UPDATE relationship SET revision=revision+1')
  with self.assertRaisesRegex(CycleError,'context_changed'):self.auto.begin(q['id'])
 def test_history_does_not_reply_even_when_enabled(self):
  self.enable();self.add('1','Il link non funziona');self.service.process_due(self.p);self.assertIsNone(self.auto.prepare(self.p,self.creator))
del ServiceTests
if __name__=='__main__':unittest.main()
