import importlib.util,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_cycle_service import ServiceTests
from lib.cycle_auto_reply import AutoReplies,ACK,REPLY_CHECK_DEADLINE,REPLY_CHECK_INTERVAL,REPLY_CHECK_MAX,reply_blocker
from lib.outreach_policy import marketing_isolated
_SPEC=importlib.util.spec_from_file_location('agent_reply_worker_recovery',Path(__file__).resolve().parents[1]/'scripts/run-agent-replies.py')
WORKER=importlib.util.module_from_spec(_SPEC);_SPEC.loader.exec_module(WORKER)
SENDER={'account':'acc6','identity':'b'*64}
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
 def unknown_reply(self):
  q=self.handoff();self.auto.begin(q['id'],SENDER);self.auto.unknown(q['id']);return q
 def test_begin_freezes_the_original_sender_before_submission(self):
  q=self.handoff()
  with self.assertRaisesRegex(CycleError,'reply_sender_invalid'):self.auto.begin(q['id'],{'account':'acc6'})
  self.assertEqual(self.auto.get(q['id'])['state'],'ready')
  self.auto.begin(q['id'],SENDER);row=self.auto.get(q['id'])
  self.assertEqual((row['state'],row['sender_account'],row['sender_identity']),('inflight','acc6','b'*64))
 def test_unknown_reply_is_read_boundedly_then_isolated_to_its_creator_only(self):
  q=self.unknown_reply();reads=[]
  def read(_store,replies,reply,_market,_stage):
   reads.append((reply['id'],reply['sender_account']));return {'state':replies.get(reply['id'])['state']}
  with patch.object(WORKER,'run_existing',side_effect=read):
   self.assertEqual(WORKER.recover_unresolved(self.s,self.p,'it','full')['state'],'waiting_reply_check')
   self.assertEqual(reads,[])
   for attempt in range(1,REPLY_CHECK_MAX+1):
    self.now+=REPLY_CHECK_INTERVAL+1
    result=WORKER.recover_unresolved(self.s,self.p,'it','full')
    self.assertEqual((result['state'],result['attempt']),('original_intent_rechecked',attempt))
    # A restart keeps the spent budget: a new ledger reads the same durable counters.
    self.assertEqual(AutoReplies(self.s).get(q['id'])['check_attempts'],attempt)
   self.now+=REPLY_CHECK_INTERVAL+1
   self.assertIsNone(WORKER.recover_unresolved(self.s,self.p,'it','full'))
  self.assertEqual(reads,[(q['id'],'acc6')]*REPLY_CHECK_MAX)
  row=self.auto.get(q['id'])
  self.assertEqual((row['state'],row['isolation_reason'],row['request_ref'],row['text']),
                   ('isolated','reply_unknown_budget_exhausted',q['request_ref'],q['text']))
  self.assertIsNone(reply_blocker(self.s.db,self.p))
  self.assertEqual(reply_blocker(self.s.db,self.p,self.creator),'reply_isolated')
  oec=self.rel()['oec'];self.assertTrue(marketing_isolated(self.s.db,self.p,self.creator,oec))
  self.assertFalse(marketing_isolated(self.s.db,self.p,'another-creator','another-oec'))
  # The commercial handoff remains with the human; the technical isolation does not close it.
  case=self.s.db.execute("SELECT state,ack_state FROM service_case WHERE id=?",(q['case_id'],)).fetchone()
  self.assertEqual((case['state'],self.rel()['mode']),('open','human'));self.assertNotEqual(case['ack_state'],'confirmed')
  with self.assertRaises(CycleError):self.auto.begin(q['id'],SENDER)
  # Exact late evidence of the original message still settles it idempotently.
  self.auto.confirm(q['id'],self.proof(q));self.auto.confirm(q['id'],self.proof(q))
  self.assertEqual(self.auto.get(q['id'])['state'],'confirmed')
 def test_deadline_isolates_after_a_real_check_and_unstarted_reads_are_refunded(self):
  q=self.unknown_reply()
  with patch.object(WORKER,'run_existing',side_effect=BlockingIOError()):
   self.now+=REPLY_CHECK_INTERVAL+1
   self.assertEqual(WORKER.recover_unresolved(self.s,self.p,'it','full')['state'],'reply_check_deferred')
  self.assertEqual(self.auto.get(q['id'])['check_attempts'],0)
  self.now+=REPLY_CHECK_DEADLINE
  self.assertEqual(self.auto.recovery_step(q['id'])['action'],'check')
  with patch.object(WORKER,'run_existing',side_effect=CycleError('reply_original_identity_changed')):
   result=WORKER.recover_unresolved(self.s,self.p,'it','full')
  self.assertEqual((result['replyState'],result['error']),('unknown','reply_original_identity_changed'))
  self.assertEqual(self.auto.recovery_step(q['id'])['action'],'isolate')
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
 def test_manual_text_is_idempotent_and_does_not_need_agent_switch(self):
  self.ingest([]);revision=self.rel()['revision']
  q=self.auto.prepare_manual(self.p,self.creator,'10','Ciao',revision,'manual-request')
  self.assertEqual(self.auto.prepare_manual(self.p,self.creator,'10','Ciao',revision,'manual-request')['id'],q['id'])
  with self.assertRaisesRegex(CycleError,'request_conflict'):
   self.auto.prepare_manual(self.p,self.creator,'10','Testo diverso',revision,'manual-request')
  self.assertEqual(self.auto.begin(q['id'])['componentKind'],'text')
 def test_manual_card_has_its_own_component_scope(self):
  self.ingest([]);q=self.auto.prepare_manual_card(self.p,self.creator,'10',{'pid':'1','listId':'2'},self.rel()['revision'],'manual-card-request')
  self.assertEqual(self.auto.begin(q['id'])['componentKind'],'card')
 def test_existing_manual_intent_can_be_recovered_after_control_changes(self):
  self.ingest([]);revision=self.rel()['revision'];q=self.auto.prepare_manual(self.p,self.creator,'10','Ciao',revision,'manual-recovery')
  self.s.db.execute('UPDATE relationship SET revision=revision+1')
  self.assertEqual(self.auto.prepare_manual(self.p,self.creator,'10','Ciao',revision,'manual-recovery')['id'],q['id'])
  with self.assertRaisesRegex(CycleError,'request_conflict'):
   self.auto.prepare_manual(self.p,self.creator,'10','Altro',revision,'manual-recovery')
del ServiceTests
if __name__=='__main__':unittest.main()
