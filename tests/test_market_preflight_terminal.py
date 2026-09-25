import json,unittest
from types import SimpleNamespace
from unittest.mock import patch
import test_cycle_delivery as fixture
from lib.second_cycle import CycleError
from lib.market_send_canary import _preflight_or_close,_unsettled

class PreflightTerminalTests(unittest.TestCase):
 setUp=fixture.DeliveryTests.setUp
 tearDown=fixture.DeliveryTests.tearDown
 def intent(self,state='confirmed'):
  q=self.d.prepare_conversation(self.id)
  self.s.db.execute('UPDATE cycle_conversation_intent SET state=?,cid=?,receipt=? WHERE delivery_id=?',(state,'99',json.dumps({'requestRef':q['request_ref'],'conversationId':'99'}),self.id))
  return tuple(self.s.db.execute('SELECT * FROM cycle_conversation_intent').fetchone())
 def blocked(self,reason='unknown_message_needs_review'):
  with patch('lib.market_send_canary._preflight_conversation',side_effect=CycleError(reason)):
   return _preflight_or_close(self.s,None,self.p,self.c,SimpleNamespace(conversation_id='99'),self.id)
 def test_unknown_historical_sender_cancels_only_unsubmitted_pair(self):
  old=self.intent();snapshot=self.d.get(self.id)['snapshot'];parts=[p['request_ref'] for p in self.d.get(self.id)['parts']]
  self.assertEqual(self.blocked()['state'],'cancelled')
  d=self.d.get(self.id);self.assertEqual([p['state'] for p in d['parts']],['cancelled','cancelled'])
  self.assertEqual(d['snapshot'],snapshot);self.assertEqual([p['request_ref'] for p in d['parts']],parts)
  self.assertEqual(tuple(self.s.db.execute('SELECT * FROM cycle_conversation_intent').fetchone()),old)
  proof=json.loads(self.s.db.execute('SELECT payload FROM cycle_delivery_check').fetchone()[0]);self.assertEqual(proof['reason'],'unknown_message_needs_review');self.assertEqual(proof['platformWrites'],0)
 def test_confirmed_conversation_does_not_make_expired_unsent_pair_immortal(self):
  self.d.cancel_unsubmitted(self.id,'test_replaced')
  frozen=self.c|{'source':{'sourceId':'test-expired-confirmed'},'executionMode':'market-continuous-v1'}
  self.id=self.d.prepare(self.p,frozen)['id'];old=self.intent();self.now+=1801
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,('market-continuous-v1',)),[self.id])
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,('market-continuous-v1',)),[])
  self.assertEqual(tuple(self.s.db.execute('SELECT * FROM cycle_conversation_intent').fetchone()),old)
 def test_inflight_or_received_conversation_cannot_be_cancelled(self):
  for state in ('inflight','received'):
   with self.subTest(state=state):
    self.intent(state)
    with self.assertRaisesRegex(CycleError,'delivery_cancel_not_safe'):self.blocked()
    self.assertEqual(self.d.get(self.id)['state'],'ready')
 def test_unknown_component_is_never_cancelled_or_reclassified(self):
  self.intent();self.s.db.execute("UPDATE cycle_delivery_part SET state='unknown',started=? WHERE delivery_id=? AND kind='card'",(self.now,self.id))
  with self.assertRaisesRegex(CycleError,'delivery_cancel_not_safe'):self.blocked()
  self.assertEqual(self.d.get(self.id)['parts'][0]['state'],'unknown')
 def test_shared_errors_still_propagate_and_do_not_cancel(self):
  self.intent()
  with self.assertRaisesRegex(CycleError,'account_unavailable'):self.blocked('account_unavailable')
  self.assertEqual(self.d.get(self.id)['state'],'ready')
 def test_all_recipient_terminal_codes_close_ready_pair(self):
  from lib.continuous_send import PREFLIGHT_TERMINAL
  for reason in PREFLIGHT_TERMINAL:
   with self.subTest(reason=reason):
    with patch.object(self.d.__class__,'get',return_value={'state':'ready','parts':[]}),patch.object(self.d.__class__,'cancel_unsubmitted') as close:
     self.assertEqual(self.blocked(reason)['state'],'cancelled');close.assert_called_once_with(self.id,reason)
