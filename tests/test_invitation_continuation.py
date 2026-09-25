import json,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lib.second_cycle import CycleStore,CycleError,digest
from lib.cycle_delivery import Deliveries
from lib.cycle_inbox import Inbox
from lib.cycle_service import Service
from lib.cycle_auto_reply import AutoReplies
from lib.schema_migrations import apply_database
from lib.invitation_continuation import allowed,local_permit_error,CHECK
from lib.market_send_canary import _preflight_conversation,_preflight_or_close
from lib.observed_messages import replied_after,outbound_messages
from lib.reply_events import backfill
from lib.agent_reply_v2 import production_context
from test_second_cycle import NOW,offer,edge
ROOT=Path(__file__).resolve().parents[1]

class ContinuationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=NOW
  self.s=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now)
  self.d=Deliveries(self.s);self.inbox=Inbox(self.s);self.service=Service(self.s);AutoReplies(self.s)
  apply_database(self.root,'second-cycle')
  self.plan=self.s.plan('bjn-local-research','it');self.make(self.plan,'it')
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def make(self,plan,market,cid="88"):
  self.s.publish(plan,'source',self.now,[offer(endAt=NOW+80*86400)])
  self.s.import_edges(plan,[edge()])
  self.c={'creatorId':'c1','oecId':'123','pid':'1','planRevision':1,'controlRevision':1,
   'offer':offer(endAt=NOW+80*86400),'source':{'sourceId':'e1'},'conversationId':cid,
   'executionMode':'continuous-v1' if market=='it' else 'market-continuous-v1',
   'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'Original invitation'}}
  self.did=self.d.prepare(plan,self.c)['id'];self.plan=plan
  self.begin('card');self.d.confirm(self.did,'card',self.proof('card','101'))
 def begin(self,kind):return self.d.begin(self.did,kind,authorized_snapshot_hash=digest(self.c),recipient_verified=True,allowance_verified=True)
 def proof(self,kind,mid):
  part=next(p for p in self.d.get(self.did)['parts'] if p['kind']==kind)
  return {'status':'confirmed','requestRef':part['request_ref'],'oecId':'123','kind':kind,'messageId':mid,'evidenceRef':'exact-original'}
 def event(self,mid='201',kind='creatorReplies',cid='88',stamp=None):
  return {'conversationId':cid,'oecId':'123','messageId':mid,'kind':kind,'createTimeRaw':int((self.now if stamp is None else stamp)*1000)}
 def ingest(self,events,cid='88',more=False):return self.inbox.ingest(self.plan,cid,'123',{'identityVerified':True,'hasMore':more,'events':events})
 def incoming(self,mid='201'):
  self.now+=1;e=self.event(mid);result=self.ingest([e]);self.service.capture(self.plan,'88','123',[{'messageId':mid,'format':'text','text':'A real question?','nativeType':'text','rawSha256':'question-'+mid}]);return result
 def test_first_reply_after_card_is_live_and_same_frozen_text_completes_in_all_markets(self):
  for market in ('it','br','my','uk'):
   with self.subTest(market=market):
    if market!='it':self.make(self.s.plan('bjn-local-research',market),market)
    frozen=self.d.get(self.did)['snapshot'];result=self.incoming()
    self.assertEqual(result['liveReplies'],1);self.assertTrue(allowed(self.s,self.did))
    pending=tuple(self.s.db.execute('SELECT * FROM inbox_pending WHERE plan_id=?',(self.plan,)).fetchone())
    self.assertFalse(self.d.interrupted_by_inquiry(self.did));self.begin('text');self.d.confirm(self.did,'text',self.proof('text','301'))
    self.assertEqual(self.d.get(self.did)['state'],'confirmed');self.assertEqual(self.d.get(self.did)['snapshot'],frozen)
    self.assertEqual(pending,tuple(self.s.db.execute('SELECT * FROM inbox_pending WHERE plan_id=?',(self.plan,)).fetchone()))
    self.assertGreater(self.s.db.execute('SELECT inbox_until FROM relationship WHERE plan_id=?',(self.plan,)).fetchone()[0],0)
 def test_manual_pause_resume_revision_cannot_be_disguised_as_new_inbound(self):
  self.incoming();self.assertTrue(allowed(self.s,self.did))
  self.s.db.execute("UPDATE relationship SET mode='paused',revision=revision+1 WHERE plan_id=?",(self.plan,))
  self.s.db.execute("UPDATE relationship SET mode='auto',revision=revision+1 WHERE plan_id=?",(self.plan,))
  self.incoming('202');self.assertFalse(allowed(self.s,self.did))
  with self.assertRaisesRegex(CycleError,'relationship_changed'):self.begin('text')
 def test_pause_rejection_case_expiry_unknown_and_modified_body_all_still_block(self):
  self.incoming()
  for mutation in ["mode='human'","mode='paused'","rejected=1"]:
   with self.subTest(mutation=mutation):
    self.s.db.execute('UPDATE relationship SET '+mutation+' WHERE plan_id=?',(self.plan,))
    self.assertFalse(allowed(self.s,self.did))
    with self.assertRaises(CycleError):self.begin('text')
    self.s.db.execute("UPDATE relationship SET mode='auto',rejected=0 WHERE plan_id=?",(self.plan,))
  with self.assertRaisesRegex(CycleError,'execution_authorization_missing'):
   self.d.begin(self.did,'text',authorized_snapshot_hash=digest(self.c|{'message':{'textIt':'changed'}}),recipient_verified=True,allowance_verified=True)
  self.now+=1801;self.assertFalse(allowed(self.s,self.did))
 def test_different_cid_gap_old_reply_and_unknown_card_never_grant_continuation(self):
  self.now+=1;self.ingest([],cid='99');self.now+=1;self.ingest([self.event(cid='99')],cid='99')
  self.assertFalse(allowed(self.s,self.did))
  # The different-CID revision is an unaccounted change, even when the next matching reply arrives.
  self.incoming('202');self.assertFalse(allowed(self.s,self.did))
 def test_repeated_poll_is_idempotent_and_new_messages_extend_only_inbox_chain(self):
  self.incoming();self.d=Deliveries(self.s)
  e=self.event(stamp=self.now);self.ingest([e]);self.assertTrue(allowed(self.s,self.did))
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_delivery_check WHERE kind=?',(CHECK,)).fetchone()[0],1)
  self.incoming('202');self.assertTrue(allowed(self.s,self.did))
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM cycle_delivery_check WHERE kind=?',(CHECK,)).fetchone()[0],2)
 def test_edit_of_current_live_inbound_extends_proof_without_clearing_pending(self):
  self.incoming();self.now+=1
  self.service.capture(self.plan,'88','123',[{'messageId':'201','format':'text','text':'Updated question?','nativeType':'text','rawSha256':'edited'}])
  self.assertTrue(allowed(self.s,self.did));self.begin('text')
  self.assertEqual(self.s.db.execute('SELECT state FROM inbox_pending').fetchone()[0],'awaiting_classification')
 def test_preflight_exception_does_not_ignore_history_limits_or_human_handoff(self):
  self.incoming();history={'identityVerified':True,'hasMore':False,'events':[],
   'senderCounts':{'ourMessages':1,'creatorReplies':1,'showcaseNotifications':0,'otherOrUnknown':0},
   'outboundCreateTimeRaw':[int(NOW*1000)],'outboundTimeMissingCount':0}
  session=SimpleNamespace(history_summary=lambda *a,**kw:history);conv=SimpleNamespace(conversation_id='88')
  _preflight_conversation(self.s,session,self.plan,self.c,conv,self.did)
  history['senderCounts']['otherOrUnknown']=1
  with self.assertRaisesRegex(CycleError,'unknown_message_needs_review'):_preflight_conversation(self.s,session,self.plan,self.c,conv,self.did)
  history['senderCounts']['otherOrUnknown']=0;self.s.db.execute("UPDATE relationship SET mode='human',revision=revision+1 WHERE plan_id=?",(self.plan,))
  result=_preflight_or_close(self.s,session,self.plan,self.c,conv,self.did)
  self.assertEqual(result['state'],'partial_delivery');self.assertEqual(self.d.get(self.did)['parts'][0]['state'],'confirmed')
 def test_invitation_is_not_an_operator_answer_and_live_context_has_exact_later_text(self):
  self.incoming();inbound_time=self.now;self.now+=1;self.begin('text');self.d.confirm(self.did,'text',self.proof('text','301'))
  self.ingest([self.event('301','ourMessages')]);self.service.capture(self.plan,'88','123',[{'messageId':'301','format':'text','text':'Original invitation','nativeType':'text','rawSha256':'own'}])
  self.assertFalse(replied_after(self.s.db,self.plan,'88','123',inbound_time))
  backfill(self.s);turn=self.s.db.execute('SELECT turn_id FROM inbound_turn WHERE message_id=\'201\'').fetchone()[0]
  replay=production_context(ROOT,self.s,self.plan,'it',turn)
  live=production_context(ROOT,self.s,self.plan,'it',turn,include_current_invitation=True)
  self.assertFalse(any(m.get('text')=='Original invitation' for m in replay['messages']))
  self.assertTrue(any(m.get('text')=='Original invitation' and m.get('purpose')=='outreach_invitation' for m in live['messages']))
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM turn_episode_link').fetchone()[0],1)
  from test_agent_reply_worker import WORKER
  self.assertEqual(len(WORKER.pending_rows(self.s,self.plan,self.now)),1)
  self.now+=1;self.ingest([self.event('302','ourMessages')]);self.service.capture(self.plan,'88','123',[{'messageId':'302','format':'text','text':'Original invitation','nativeType':'text','rawSha256':'operator'}])
  self.assertTrue(replied_after(self.s.db,self.plan,'88','123',inbound_time))
  self.assertEqual(WORKER.pending_rows(self.s,self.plan,self.now),[])
 def test_first_poll_after_pair_completion_preserves_between_component_inbound(self):
  self.now+=2;self.begin('text');self.d.confirm(self.did,'text',self.proof('text','301'))
  self.now+=1;result=self.ingest([self.event('201',stamp=NOW+1)])
  self.assertEqual(result['liveReplies'],1);self.assertFalse(allowed(self.s,self.did))
  self.assertEqual(self.s.db.execute('SELECT historical FROM inbox_event').fetchone()[0],0)
 def test_gap_and_unknown_components_never_allow_original_text(self):
  self.incoming();self.now+=1;self.ingest([self.event('202')],more=True)
  self.assertFalse(allowed(self.s,self.did))
  with self.assertRaises(CycleError):self.begin('text')
 def test_open_business_case_and_card_unknown_block_even_with_inbox_proof(self):
  self.incoming()
  self.s.db.execute("INSERT INTO service_case VALUES('business',?,?,'open',0,'fee_request',?,?,'not_sent')",(self.plan,'c1',self.now,self.now))
  self.assertFalse(allowed(self.s,self.did))
  with self.assertRaises(CycleError):self.begin('text')
  self.s.db.execute("UPDATE service_case SET state='closed'")
  self.s.db.execute("UPDATE cycle_delivery SET state='unknown' WHERE id=?",(self.did,))
  self.assertFalse(allowed(self.s,self.did))
  with self.assertRaises(CycleError):self.begin('text')
 def test_completed_pending_row_does_not_cancel_ready_text_by_its_presence(self):
  self.s.db.execute("INSERT INTO inbox_pending VALUES(?,? ,1,?,'answered')",(self.plan,'c1',self.now))
  self.assertFalse(self.d.interrupted_by_inquiry(self.did));self.assertEqual(self.d.get(self.did)['parts'][1]['state'],'ready')
 def test_executor_sends_only_original_text_once_and_stop_still_prevents_dispatch(self):
  from contextlib import contextmanager
  from lib.cycle_executor import execute
  from lib.italy_im_delivery import ItalyImDeliveryError
  calls=[];halt=[False]
  @contextmanager
  def gate():yield lambda:None
  class Adapter:
   def send_once(_,conv,text,ref,before_dispatch):
    try:before_dispatch({'oecId':'123','conversationId':'88','market':'it','account':'acc6','requestRef':ref,'componentKind':'text','stage':'send_message'})
    except CycleError:raise ItalyImDeliveryError('it_delivery_dispatch_not_allowed') from None
    calls.append((text,ref));return {'requestRef':ref,'messageId':'301'}
   def readback(_,conv,text,ref,**kwargs):return {'status':'confirmed','messageId':'301','evidenceRef':'exact'}
  @contextmanager
  def runtime(c,read_only=False):
   yield {'adapter':Adapter(),'reads':SimpleNamespace(conversation=lambda *a:SimpleNamespace(conversation_id='88')),'card':None,'validate_card':lambda c:None,'write_gate':gate}
  def authorize(c):
   if halt[0]:raise CycleError('continuous_send_stopped')
  def preflight(*args):self.incoming();halt[0]=True
  with self.assertRaisesRegex(CycleError,'continuous_send_stopped'):execute(self.d,self.did,runtime,authorize,preflight)
  self.assertEqual(calls,[]);self.assertEqual(self.d.get(self.did)['parts'][1]['state'],'ready')
  halt[0]=False
  before=self.d.get(self.did)['parts'][1]['request_ref'];execute(self.d,self.did,runtime,authorize,lambda *a:None)
  execute(self.d,self.did,runtime,authorize,lambda *a:None)
  self.assertEqual(calls,[('Original invitation',before)])
  self.assertEqual(self.s.db.execute('SELECT state FROM inbox_pending').fetchone()[0],'awaiting_classification')
 def test_question_still_blocks_new_pid_but_does_not_block_other_creators(self):
  self.incoming();self.begin('text');self.d.confirm(self.did,'text',self.proof('text','301'))
  with self.assertRaisesRegex(CycleError,'relationship_changed'):
   self.d.prepare(self.plan,self.c|{'source':{'sourceId':'another-product'}})
  self.s.import_edges(self.plan,[edge(person='c2',source='other-creator')])
  candidate=self.c|{'creatorId':'c2','oecId':'456','source':{'sourceId':'other-creator'},'conversationId':'99'}
  other=self.d.prepare(self.plan,candidate)
  self.d.begin(other['id'],'card',authorized_snapshot_hash=digest(candidate),recipient_verified=True,allowance_verified=True)
  self.assertEqual(self.d.get(other['id'])['parts'][0]['state'],'inflight')
 def test_continuation_cannot_switch_original_sender_or_im_identity(self):
  from lib.invitation_continuation import require_original_sender
  for market,account in [('br','acc1'),('my','acc8'),('uk','acc11')]:
   require_original_sender({},market,account,'old-im')
   with self.assertRaisesRegex(CycleError,'original_sender_changed'):require_original_sender({},market,'replacement','old-im')
   candidate={'senderAccount':account,'senderImId':'old-im'}
   with self.assertRaisesRegex(CycleError,'original_sender_changed'):require_original_sender(candidate,market,account,'new-im')
 def test_invitation_preserves_conversation_unread_state(self):
  from lib.conversation_workbench import list_conversations
  self.incoming();self.now+=1;self.begin('text');self.d.confirm(self.did,'text',self.proof('text','301'))
  self.ingest([self.event('301','ourMessages')]);self.service.capture(self.plan,'88','123',[{'messageId':'301','format':'text','text':'Original invitation','nativeType':'text','rawSha256':'own'}])
  backfill(self.s)
  rows=list_conversations(self.root,self.s,view='all',market='it')['items']
  row=next(r for r in rows if r['conversationId']=='88')
  self.assertTrue(row['unread'])
 def test_first_card_baseline_uses_only_confirmed_original_conversation_intent(self):
  self.make(self.s.plan('bjn-local-research','uk'),'uk',cid='')
  self.s.db.execute("INSERT INTO cycle_conversation_intent VALUES(?,?,'inflight',NULL,NULL)",(self.did,'original-create-request'))
  from lib.invitation_continuation import confirmed_contact_start
  self.assertIsNone(confirmed_contact_start(self.s.db,self.plan,'88','123'))
  self.s.db.execute("UPDATE cycle_conversation_intent SET state='received',cid='88' WHERE delivery_id=?",(self.did,))
  self.assertIsNone(confirmed_contact_start(self.s.db,self.plan,'88','123'))
  self.s.db.execute("UPDATE cycle_conversation_intent SET state='confirmed',cid='88' WHERE delivery_id=?",(self.did,))
  self.assertEqual(self.incoming()['liveReplies'],1);self.assertTrue(allowed(self.s,self.did))
 def test_local_refusal_unwrapped_only_before_component_submission(self):
  from lib.italy_im_delivery import ItalyImDeliveryError
  try:
   try:raise CycleError('relationship_changed')
   except CycleError:raise ItalyImDeliveryError('it_delivery_dispatch_not_allowed') from None
  except ItalyImDeliveryError as error:
   text=self.d.get(self.did)['parts'][1];self.assertIsInstance(local_permit_error(error,text),CycleError)
   self.assertIsNone(local_permit_error(error,dict(text,state='inflight',started=self.now)))
