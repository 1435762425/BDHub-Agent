import sys,tempfile,unittest,threading,time,json,os
from pathlib import Path
from dataclasses import replace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.im_session_owner import OwnerState,serve,borrow,OwnerUnavailable,enabled,paths
import test_italy_im_delivery as wire
from lib.italy_im_delivery import ItalyImDeliveryAdapter,ItalyImDeliveryError
from lib.second_cycle import CycleStore,CycleError
from lib.cycle_inbox import Inbox
from lib.cycle_service import Service
from lib.schema_migrations import apply_database
from lib.sdk_inbox import Receiver,read_to_overlap
from types import SimpleNamespace

class BrokerTests(unittest.TestCase):
 def setUp(self):
  # AF_UNIX path length on macOS is bounded; keep the fixture root short.
  self.tmp=tempfile.TemporaryDirectory(dir='/tmp',prefix='im-');self.root=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def owner(self,market='it',account='acc6'):
  auth=wire.auth(account_name=account);auth=replace(auth,native_context=auth.native_context|{'market':market})
  owner=OwnerState(market,account,auth,lambda:True);owner.accepting=True;return owner
 def test_all_markets_borrow_same_account_without_new_send_permission(self):
  for market,account in [('it','acc6'),('br','acc1'),('my','acc8'),('uk','acc11')]:
   with self.subTest(market=market):
    owner=self.owner(market,account)
    with serve(self.root,market,owner):
     with borrow(self.root,market,account) as (auth,check):
      self.assertEqual(auth,owner.auth);self.assertFalse(check());self.assertEqual(owner.clients,1)
      with self.assertRaises(OwnerUnavailable):
       with borrow(self.root,market,'acc99'):pass
     deadline=time.monotonic()+1
     while owner.clients and time.monotonic()<deadline:time.sleep(.01)
     self.assertEqual(owner.clients,0)
    self.assertFalse(paths(self.root,market)[0].exists())
 def test_body_business_error_not_reclassified_or_swallowed(self):
  with serve(self.root,'it',self.owner()):
   with self.assertRaisesRegex(CycleError,'business_failure'):
    with borrow(self.root,'it','acc6'):raise CycleError('business_failure')
 def test_drain_waits_for_existing_client_and_refuses_new(self):
  owner=self.owner()
  with serve(self.root,'it',owner):
   with borrow(self.root,'it','acc6') as (_,check):
    drain=threading.Thread(target=owner.drain);drain.start();time.sleep(.03)
    self.assertTrue(drain.is_alive());self.assertFalse(check())
    with self.assertRaises(OwnerUnavailable):
     with borrow(self.root,'it','acc6'):pass
   drain.join(1);self.assertFalse(drain.is_alive())
 def test_generation_or_stopped_owner_invalidates_borrow(self):
  owner=self.owner()
  with serve(self.root,'it',owner):
   with borrow(self.root,'it','acc6') as (_,check):
    owner.valid=lambda:False
    with self.assertRaises(OwnerUnavailable):check()
 def test_socket_permissions_are_private_and_rechecked(self):
  with serve(self.root,'it',self.owner()):
   p=paths(self.root,'it')[0];self.assertEqual(p.stat().st_mode&0o777,0o600);p.chmod(0o666)
   with self.assertRaises(OwnerUnavailable):
    with borrow(self.root,'it','acc6'):pass
 def test_missing_owner_fails_closed(self):
  with self.assertRaises(OwnerUnavailable):
   with borrow(self.root,'it','acc6'):pass
 def test_mode_is_opt_in_and_invalid_mode_fails(self):
  self.assertFalse(enabled(self.root,'it'));(self.root/'config').mkdir();p=self.root/'config/market-accounts.json'
  p.write_text(json.dumps({'markets':{'it':{'imSessionMode':'unknown'}}}))
  with self.assertRaises(ValueError):enabled(self.root,'it')

class SignalTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1790300000
  self.s=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now);self.plan=self.s.plan('bjn-local-research','my');apply_database(self.root,'second-cycle')
  self.inbox=Inbox(self.s);self.service=Service(self.s);self.s.db.execute("INSERT INTO relationship VALUES(?,?,?,'auto',0,1,0,1)",(self.plan,'c','123'))
  self.inbox.ingest(self.plan,'999','123',{'identityVerified':True,'hasMore':False,'events':[]})
  self.receiver=Receiver(self.root,'my',SimpleNamespace(account_name='acc8'),lambda:False,lambda:True)
 def tearDown(self):self.s.close();self.tmp.cleanup()
 def incoming(self,text='hello'):
  self.now+=1
  self.inbox.ingest(self.plan,'999','123',{'identityVerified':True,'hasMore':False,'events':[{'conversationId':'999','oecId':'123','messageId':'1','kind':'creatorReplies','messageType':1000,'createTimeRaw':self.now*1000}]})
  self.service.capture(self.plan,'999','123',[{'messageId':'1','format':'text','text':text,'nativeType':'text','rawSha256':'a'}])
 def test_startup_replay_does_not_reopen_pending_or_duplicate_messages(self):
  self.incoming();before=[tuple(r) for r in self.s.db.execute('select * from inbox_pending')]
  self.receiver.signal([{'cid':'999','mid':'1','text':'hello'}]*3)
  self.assertEqual(before,[tuple(r) for r in self.s.db.execute('select * from inbox_pending')]);self.assertEqual(self.s.db.execute('select count(*) from im_receive_signal').fetchone()[0],0)
 def test_known_cards_and_attachments_are_not_false_text_edits(self):
  self.incoming()
  self.service.capture(self.plan,'999','123',[{'messageId':'1','format':'attachment_or_unsupported','text':None,'nativeType':'product_list','rawSha256':'card'}])
  self.receiver.signal([{'cid':'999','mid':'1','text':'[商品列表]'}])
  self.assertEqual(self.s.db.execute('select count(*) from im_receive_signal').fetchone()[0],0)
 def test_new_signal_is_durable_idempotent_and_does_not_fake_inbound(self):
  event={'cid':'999','mid':'2','text':'new'};self.receiver.signal([event,event]);self.assertEqual(self.s.db.execute('select count(*) from im_receive_signal').fetchone()[0],1)
  self.assertEqual(self.s.db.execute('select count(*) from inbox_event').fetchone()[0],0)
  reopened=Receiver(self.root,'my',SimpleNamespace(account_name='acc8'),lambda:False,lambda:True);reopened.signal([event]);self.assertEqual(self.s.db.execute('select count(*) from im_receive_signal').fetchone()[0],1)
 def test_edited_existing_message_wakes_http_without_overwriting_body(self):
  self.incoming();self.receiver.signal([{'cid':'999','mid':'1','text':'edited'}]);self.assertEqual(self.s.db.execute('select count(*) from im_receive_signal where done=0').fetchone()[0],1)
  self.assertEqual(self.service.context(self.plan,'c')[0]['content']['text'],'hello')
 def test_unbound_creator_is_not_imported_from_sdk_assertion(self):
  self.receiver.signal([{'cid':'12345','mid':'2','oec':'8888','text':'new'}]);self.assertEqual(self.s.db.execute('select count(*) from im_receive_signal').fetchone()[0],0)
 def test_backfill_reads_until_known_overlap_without_clearing_gaps(self):
  self.incoming();calls=[]
  pages=[{'identityVerified':True,'events':[{'messageId':'2'}],'contents':[],'hasMore':True,'nextCursor':'10'}, {'identityVerified':True,'events':[{'messageId':'1'}],'contents':[],'hasMore':True,'nextCursor':'20'}]
  def read(*a,**kw):calls.append(kw['cursor']);return pages[len(calls)-1]
  combined=read_to_overlap(SimpleNamespace(history_summary=read),SimpleNamespace(conversation_id='999'),self.s,self.plan)
  self.assertEqual(calls,[0,10]);self.assertEqual([x['messageId'] for x in combined['events']],['2','1'])
  self.assertTrue(combined['hasMore'])
