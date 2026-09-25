import sys,unittest,tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib import cycle_delivery
from lib.cycle_delivery import Deliveries
from lib.market_send_canary import _record_create_failure,_record_send_failure,_received_conversation_id
from test_second_cycle import offer,edge,NOW
class DeliveryTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.now=NOW;self.s=CycleStore(Path(self.t.name)/'db',lambda:self.now);self.p=self.s.plan('test','it');self.s.publish(self.p,'s',NOW,[offer()]);self.s.import_edges(self.p,[edge()]);r=self.s.db.execute('SELECT * FROM relationship').fetchone()
  self.c={'creatorId':r['creator_id'],'oecId':r['oec'],'pid':offer()['pid'],'source':{'sourceId':edge()['sourceId']},'offer':offer(),'planRevision':1,'controlRevision':1,'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'Hello'}};self.d=Deliveries(self.s);self.id=self.d.prepare(self.p,self.c)['id']
 def tearDown(self):self.s.close();self.t.cleanup()
 def begin(self,kind):return self.d.begin(self.id,kind,authorized_snapshot_hash=digest(self.c),recipient_verified=True,allowance_verified=True)
 def proof(self,kind):return {'status':'confirmed','requestRef':next(p['request_ref'] for p in self.d.get(self.id)['parts'] if p['kind']==kind),'oecId':self.c['oecId'],'messageId':'10','evidenceRef':'verified','kind':kind}
 def test_b_source_expiry_is_rechecked_before_platform_write(self):
  from lib.video_window import bounds
  self.d.cancel_unsubmitted(self.id,'test_replaced')
  candidate={**self.c,'source':{'sourceId':'video-source-expiry','sourceClass':'B','videoReleasedAt':str(bounds(self.now)[0])}}
  did=self.d.prepare(self.p,candidate)['id']
  self.now+=86400
  with self.assertRaisesRegex(CycleError,'video_lead_expired'):
   self.d.begin(did,'card',authorized_snapshot_hash=digest(candidate),recipient_verified=True,allowance_verified=True)
  self.assertEqual(self.d.get(did)['parts'][0]['state'],'ready')
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
 def test_expired_deliveries_are_settled_only_when_nothing_was_dispatched(self):
  self.s.db.execute('DELETE FROM cycle_delivery_part');self.s.db.execute('DELETE FROM cycle_delivery')
  mode='market-continuous-v1';did=self.d.prepare(self.p,{**self.c,'executionMode':mode})['id']
  self.d.prepare_conversation(did)
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,(mode,)),[])
  self.now+=1801
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,('continuous-v1',)),[])
  self.s.db.execute("UPDATE cycle_conversation_intent SET state='inflight' WHERE delivery_id=?",(did,))
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,(mode,)),[])
  self.assertEqual(self.d.get(did)['state'],'ready')
  self.s.db.execute("UPDATE cycle_conversation_intent SET state='ready' WHERE delivery_id=?",(did,))
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,(mode,)),[did])
  self.assertEqual({self.d.get(did)['state'],*(part['state'] for part in self.d.get(did)['parts'])},{'cancelled'})
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,(mode,)),[])
 def test_expired_delivery_with_a_delivered_card_keeps_the_card_and_drops_the_late_text(self):
  self.s.db.execute('DELETE FROM cycle_delivery_part');self.s.db.execute('DELETE FROM cycle_delivery')
  mode='market-continuous-v1';self.c={**self.c,'executionMode':mode};did=self.d.prepare(self.p,self.c)['id'];self.id=did
  self.begin('card')
  self.now+=1801
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,(mode,)),[])
  self.now-=1801;self.d.confirm(did,'card',self.proof('card'));self.now+=1801
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,(mode,)),[did])
  self.assertEqual((self.d.get(did)['state'],[p['state'] for p in self.d.get(did)['parts']]),('partial_delivery',['confirmed','cancelled']))
  self.assertEqual(self.d.cancel_expired_unsubmitted(self.p,(mode,)),[])
 def absence(self,reason='it_delivery_history_not_found'):
  self.d.record_check(self.id,'card',{'status':'result_unknown','reason':reason,'messageId':None,'evidenceRef':'history'})
 def test_card_missing_from_two_history_reads_is_marketing_isolated(self):
  self.begin('card');self.d.unknown(self.id,'card')
  self.absence();self.absence('it_delivery_history_unavailable')
  self.now+=400;self.absence('it_delivery_history_unavailable')
  self.assertFalse(self.d.quarantine_absent_card(self.id))
  self.absence()
  self.assertTrue(self.d.quarantine_absent_card(self.id))
  self.assertEqual(self.d.get(self.id)['state'],'quarantined_unknown')
  self.assertEqual([p['state'] for p in self.d.get(self.id)['parts']],['unknown','ready'])
  self.assertEqual(self.s.db.execute('SELECT mode FROM relationship WHERE plan_id=? AND creator_id=?',(self.p,self.c['creatorId'])).fetchone()[0],'auto')
  self.assertFalse(self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='service_case'").fetchone())
  self.assertFalse(self.d.quarantine_absent_card(self.id))
 def test_isolated_card_no_longer_blocks_the_next_creator(self):
  self.begin('card');self.d.unknown(self.id,'card');self.absence();self.now+=400;self.absence()
  self.s.import_edges(self.p,[edge(person='c2',source='e2')])
  other=self.s.db.execute("SELECT * FROM relationship WHERE creator_id='c2'").fetchone()
  second={**self.c,'creatorId':other['creator_id'],'oecId':other['oec'],'source':{'sourceId':'e2'},'controlRevision':other['revision']}
  second_id=self.d.prepare(self.p,second)['id']
  begin=lambda:self.d.begin(second_id,'card',authorized_snapshot_hash=digest(second),recipient_verified=True,allowance_verified=True)
  with self.assertRaisesRegex(CycleError,'delivery_unknown'):begin()
  self.assertTrue(self.d.quarantine_absent_card(self.id))
  self.assertEqual(begin()['kind'],'card')
  self.d.unknown(self.id,'card')
  self.assertEqual(self.d.get(self.id)['state'],'quarantined_unknown')
 def refused_create(self,native_status):
  self.s.db.execute('DELETE FROM cycle_delivery_part');self.s.db.execute('DELETE FROM cycle_delivery')
  mode='market-continuous-v1';self.c={**self.c,'executionMode':mode};self.id=self.d.prepare(self.p,self.c)['id']
  self.d.prepare_conversation(self.id);self.d.begin_conversation(self.id,digest(self.c));self.d.unknown(self.id,'card')
  self.s.db.execute("INSERT INTO cycle_platform_signal(delivery_id,at,outcome,code,native_status) VALUES(?,?,?,?,?)",
                    (self.id,self.now,'result_unknown','it_delivery_create_unknown',native_status))
  return mode
 def test_create_refused_with_201_isolates_the_creator_without_sending(self):
  mode=self.refused_create(201)
  self.assertEqual(self.d.quarantine_refused_creates(self.p,('continuous-v1',)),[])
  self.assertEqual(self.d.quarantine_refused_creates(self.p,(mode,)),[self.id])
  self.assertEqual(self.d.get(self.id)['state'],'quarantined_unknown')
  self.assertEqual([p['state'] for p in self.d.get(self.id)['parts']],['ready','ready'])
  self.assertEqual(self.s.db.execute('SELECT mode FROM relationship WHERE plan_id=? AND creator_id=?',(self.p,self.c['creatorId'])).fetchone()[0],'human')
  self.assertEqual(tuple(self.s.db.execute('SELECT reason,state FROM service_case WHERE plan_id=? AND creator_id=?',(self.p,self.c['creatorId'])).fetchone()),('conversation_business_rejected','open'))
  self.assertEqual(self.d.quarantine_refused_creates(self.p,(mode,)),[])
 def test_other_create_codes_stay_unknown(self):
  mode=self.refused_create(500)
  self.assertEqual(self.d.quarantine_refused_creates(self.p,(mode,)),[])
  self.assertEqual(self.d.get(self.id)['state'],'unknown')
 def test_absence_reads_close_together_do_not_isolate(self):
  self.begin('card');self.d.unknown(self.id,'card')
  for _ in range(3):self.absence()
  self.now+=299;self.absence()
  self.assertFalse(self.d.quarantine_absent_card(self.id))
  self.assertEqual(self.d.get(self.id)['state'],'unknown')
 def test_market_send_failure_settles_refusals_and_keeps_the_platform_answer(self):
  refusal=SimpleNamespace(outcome='rejected',code='it_delivery_send_rejected',native_status=2,check_code=7,
                          check_message='daily limit',response_ref='im-response:refused')
  _record_send_failure(self.s,self.id,'card',refusal)
  self.assertEqual(self.d.get(self.id)['state'],'ready')
  self.begin('card')
  _record_send_failure(self.s,self.id,'card',refusal)
  self.assertEqual(self.d.get(self.id)['state'],'rejected')
  self.assertEqual([p['state'] for p in self.d.get(self.id)['parts'] if p['kind']=='card'],['rejected'])
  self.assertEqual(tuple(self.s.db.execute("SELECT count(*),max(native_status),max(check_message) FROM cycle_platform_signal WHERE delivery_id=? AND outcome='rejected'",(self.id,)).fetchone()),(2,2,'daily limit'))
 def test_market_send_failure_without_a_clear_answer_waits_for_readback(self):
  self.begin('card')
  _record_send_failure(self.s,self.id,'card',RuntimeError('socket closed'))
  self.assertEqual(self.d.get(self.id)['state'],'unknown')
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_platform_signal').fetchone()[0],0)
 def test_contact_reservation_is_shared_and_not_recounted(self):
  self.d.reserve_contact(self.id);self.d.reserve_contact(self.id);self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_contact_reservation').fetchone()[0],1)
 def test_contact_capacity_blocks_before_any_message(self):
  self.s.db.executemany('INSERT INTO cycle_contact_reservation VALUES(?,?,?)',[(self.p,'other'+str(i),self.now) for i in range(500)])
  with mock.patch.object(cycle_delivery,'NEW_CONTACT_LIMIT',500):
   with self.assertRaisesRegex(CycleError,'capacity_reached'):self.d.reserve_contact(self.id)
 def test_without_a_local_cap_only_todays_platform_rejections_hold_new_contacts(self):
  self.s.db.executemany('INSERT INTO cycle_contact_reservation VALUES(?,?,?)',[(self.p,'other'+str(i),self.now) for i in range(600)])
  self.assertTrue(self.d.contact_capacity_available(self.id))
  midnight=datetime.fromtimestamp(self.now,timezone(timedelta(hours=8))).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
  signal="INSERT INTO cycle_platform_signal(delivery_id,at,outcome,code,native_status) VALUES(?,?,?,?,?)"
  self.s.db.executemany(signal,[(self.id,midnight-60,'rejected','it_delivery_send_rejected',1)]*3)
  self.s.db.execute(signal,(self.id,self.now,'result_unknown','it_delivery_create_unknown',None))
  self.assertTrue(self.d.contact_capacity_available(self.id))
  self.s.db.execute(signal,(self.id,self.now,'rejected','it_delivery_send_rejected',1))
  self.assertFalse(self.d.contact_capacity_available(self.id))
  with self.assertRaisesRegex(CycleError,'capacity_reached'):self.d.reserve_contact(self.id)
  self.assertTrue(all(p['started'] is None for p in self.d.get(self.id)['parts']))
 def test_known_preflight_exclusion_cancels_only_an_unsubmitted_delivery(self):
  self.d.reserve_contact(self.id)
  cancelled=self.d.cancel_unsubmitted(self.id,'conversation_needs_content_review')
  self.assertEqual(cancelled['state'],'cancelled')
  self.assertTrue(all(p['state']=='cancelled' and p['started'] is None for p in cancelled['parts']))
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_contact_reservation').fetchone()[0],0)
  evidence=self.s.db.execute("SELECT payload FROM cycle_delivery_check WHERE delivery_id=? AND kind='preflight'",(self.id,)).fetchone()[0]
  self.assertEqual(__import__('json').loads(evidence)['reason'],'conversation_needs_content_review')
  self.assertEqual(self.d.cancel_unsubmitted(self.id,'conversation_needs_content_review')['state'],'cancelled')
  # Once a component started, cancellation cannot erase an uncertain external effect.
  self.s.import_edges(self.p,[edge(person='c2',source='e2')]);candidate=self.c|{'creatorId':'c2','oecId':'456','source':{'sourceId':'e2'}}
  did=self.d.prepare(self.p,candidate)['id'];self.d.begin(did,'card',authorized_snapshot_hash=digest(candidate),recipient_verified=True,allowance_verified=True)
  with self.assertRaisesRegex(CycleError,'cancel_not_safe'):self.d.cancel_unsubmitted(did,'conversation_needs_content_review')
 def test_operator_can_isolate_unconfirmed_conversation_without_erasing_original_intent(self):
  intent=self.d.prepare_conversation(self.id)
  self.d.begin_conversation(self.id,digest(self.c))
  self.d.unknown(self.id,'card')
  result=self.d.quarantine_unknown_conversation(self.id,'quarantine-it-20260923',
                                                observed_conversations=748,matching_conversations=0)
  self.assertEqual(result['state'],'quarantined_unknown')
  self.assertEqual([(p['kind'],p['state'],p['started']) for p in result['parts']],
                   [('card','ready',None),('text','ready',None)])
  self.assertEqual(self.d.conversation_intent(self.id)['request_ref'],intent['request_ref'])
  self.assertEqual(self.d.conversation_intent(self.id)['state'],'inflight')
  self.assertEqual(self.s.db.execute('SELECT mode FROM relationship WHERE plan_id=? AND creator_id=?',
                                     (self.p,self.c['creatorId'])).fetchone()[0],'auto')
  self.assertFalse(self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='service_case'").fetchone())
  self.assertEqual(self.d.quarantine_unknown_conversation(self.id,'quarantine-it-20260923',
                   observed_conversations=748,matching_conversations=0)['state'],'quarantined_unknown')
  with self.assertRaisesRegex(CycleError,'quarantine_request_conflict'):
   self.d.quarantine_unknown_conversation(self.id,'different-request-id',observed_conversations=748,matching_conversations=0)
 def test_quarantine_rejects_started_component_or_unverified_platform_scope(self):
  self.d.prepare_conversation(self.id)
  self.d.begin_conversation(self.id,digest(self.c))
  self.d.unknown(self.id,'card')
  with self.assertRaisesRegex(CycleError,'quarantine_evidence_invalid'):
   self.d.quarantine_unknown_conversation(self.id,'quarantine-it-20260923',
                                          observed_conversations=748,matching_conversations=1)
  self.s.db.execute("UPDATE cycle_delivery_part SET state='inflight',started=? WHERE delivery_id=? AND kind='card'",
                    (self.now,self.id))
  with self.assertRaisesRegex(CycleError,'quarantine_scope_changed'):
   self.d.quarantine_unknown_conversation(self.id,'quarantine-it-20260923',
                                          observed_conversations=748,matching_conversations=0)
 def test_ambiguous_create_keeps_original_intent_and_redacted_platform_signal(self):
  self.d.prepare_conversation(self.id)
  self.d.begin_conversation(self.id,digest(self.c))
  error=SimpleNamespace(outcome='result_unknown',code='it_delivery_create_unknown',
                        native_status=12345,check_code=None,response_ref='it-im-create:'+'a'*64)
  self.assertTrue(_record_create_failure(self.s,self.id,error))
  self.assertEqual(self.d.get(self.id)['state'],'unknown')
  self.assertTrue(all(part['state']=='ready' and part['started'] is None for part in self.d.get(self.id)['parts']))
  self.assertEqual(self.d.conversation_intent(self.id)['state'],'inflight')
  self.assertEqual(self.s.db.execute('SELECT native_status FROM cycle_platform_signal WHERE delivery_id=?',
                                     (self.id,)).fetchone()[0],12345)
 def test_received_conversation_is_readback_confirmable_without_new_create(self):
  intent=self.d.prepare_conversation(self.id)
  self.d.begin_conversation(self.id,digest(self.c))
  self.d.save_conversation(self.id,{'requestRef':intent['request_ref'],'conversationId':'123','candidate':True})
  received=self.d.conversation_intent(self.id)
  self.assertEqual(received['state'],'received')
  self.d.confirm_conversation(self.id,_received_conversation_id(received),self.c['oecId'])
  self.assertEqual(self.d.conversation_intent(self.id)['state'],'confirmed')
  self.assertTrue(all(p['started'] is None for p in self.d.get(self.id)['parts']))
 def test_missing_quota_evidence_no_dispatch(self):
  with self.assertRaisesRegex(CycleError,'execution_evidence_missing'):self.d.begin(self.id,'card',authorized_snapshot_hash=digest(self.c),recipient_verified=True)
 def test_reserved_source_exits_ready_supply_without_deleting_history(self):
  self.assertEqual(self.s._eligible_people(self.p),set())
  self.assertGreater(self.s.db.execute('SELECT count(*) FROM source_edge').fetchone()[0],0)
 def test_bounded_cross_recipient_pipeline_still_blocks_same_creator_and_unknown(self):
  self.s.import_edges(self.p,[edge(person='c2',source='e2'),edge(person='c3',source='e3',oec='789')])
  d=Deliveries(self.s,concurrent_recipient_limit=2)
  c2=self.c|{'creatorId':'c2','oecId':'456','source':{'sourceId':'e2'}}
  c3=self.c|{'creatorId':'c3','oecId':'789','source':{'sourceId':'e3'}}
  second=d.prepare(self.p,c2)['id'];third=d.prepare(self.p,c3)['id']
  self.begin('card');d.begin(second,'card',authorized_snapshot_hash=digest(c2),recipient_verified=True,allowance_verified=True)
  with self.assertRaisesRegex(CycleError,'verify_before_dispatch'):d.begin(third,'card',authorized_snapshot_hash=digest(c3),recipient_verified=True,allowance_verified=True)
  with self.assertRaisesRegex(CycleError,'verify_before_dispatch'):d.begin(self.id,'text',authorized_snapshot_hash=digest(self.c),recipient_verified=True,allowance_verified=True)
  d.unknown(second,'card')
  with self.assertRaisesRegex(CycleError,'delivery_unknown'):d.begin(third,'card',authorized_snapshot_hash=digest(c3),recipient_verified=True,allowance_verified=True)
 def test_wrong_recipient_receipt_cannot_confirm(self):
  self.begin('card')
  with self.assertRaises(CycleError):self.d.confirm(self.id,'card',self.proof('card')|{'oecId':'other'})
if __name__=='__main__':unittest.main()
