import sys
import tempfile
import unittest
from contextlib import ExitStack,contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.account_identity import publish_generation
from lib.cycle_delivery import Deliveries
from lib.market_send_canary import run
from lib.market_send_control import control,mutate,page_start_authorization
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore,digest
from test_second_cycle import NOW,offer,edge


class MarketCanaryBindingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=NOW;self.sent=[]
  with CycleStore(self.root/'var/second-cycle.sqlite') as store:self.plan=store.plan('bjn-local-research','my')
  apply_database(self.root,'second-cycle')
  self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:self.now);self.deliveries=Deliveries(self.store)
  self.store.publish(self.plan,'source',self.now,[offer()])
  publish_generation(self.store,market='my',account='acc8',role='communications',reason='baseline',
   identity={'browserRef':'browser','httpRef':'http','imRef':'im','institutionFingerprint':'institution'},
   capabilities={'message_send':{'state':'not_tested'}})
  self.change('start','page-start-one')
  self.store.db.execute("UPDATE continuous_send_control SET window_start='00:00',window_end='24:00'")
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def change(self,action,request):
  self.now+=1
  with patch('lib.market_send_control.require_send_template_approval'):
   return mutate(self.store,ROOT,'my',action=action,request_id=request,
                 expected_revision=control(self.store,'my')['revision'])
 def restart_grant(self):self.change('stop','page-stop-one');self.change('start','page-start-two')
 def candidate(self,n,*,standalone=False,conversation=True):
  source=edge(person='c'+str(n),source='source-'+str(n));self.store.import_edges(self.plan,[source])
  return {'creatorId':source['creatorId'],'oecId':source['oec'],'pid':offer()['pid'],'source':{'sourceId':source['sourceId']},
   'offer':offer(),'planRevision':1,'controlRevision':1,'message':{'version':4,'deliveryOrder':'card_then_text','textIt':'Hello'},
   'card':{},'conversationId':'999' if conversation else None,'executionMode':'market-canary-v1','market':'my',
   'senderAccount':'acc8','senderImId':'123','pageStart':None if standalone else page_start_authorization(self.store,'my')}
 def prepare(self,candidate):return self.deliveries.prepare(self.plan,candidate)['id']
 @contextmanager
 def runtime(self,*,candidate=None,preflight=None,forbid_auth=False):
  def send(kind):
   def dispatch(_conversation,_body,ref,*,before_dispatch):
    try:before_dispatch({'market':'my','account':'acc8','componentKind':kind,'stage':'send_message'})
    except CycleError:
     from lib.italy_im_delivery import ItalyImDeliveryError
     raise ItalyImDeliveryError('it_delivery_dispatch_not_allowed') from None
    self.sent.append((kind,ref));return {'requestRef':ref,'messageId':kind+'-id'}
   return dispatch
  adapter=SimpleNamespace(send_card_once=send('card'),send_once=send('text'),
   readback_card=lambda *_a,**_k:{'status':'confirmed','messageId':'card-id','evidenceRef':'card-proof'},
   readback=lambda *_a,**_k:{'status':'confirmed','messageId':'text-id','evidenceRef':'text-proof'})
  @contextmanager
  def authenticated(*_a,**_k):
   if forbid_auth:raise AssertionError('platform_auth_forbidden')
   yield {'auth':SimpleNamespace(im_id='123'),'adapter':adapter,'partnerHost':'https://fixture.invalid',
          'session':SimpleNamespace(initialize=lambda _:{'conversations':[]},conversation=lambda *_:SimpleNamespace(conversation_id='999'))}
  @contextmanager
  def gate(*_a,**_k):yield lambda:None
  with ExitStack() as stack:
   for name,value in [('load_config',lambda _:{'markets':{'my':{'roles':{'communications':'acc8'}}}}),
      ('CycleStore',lambda path:CycleStore(path,lambda:self.now)),('authenticated',authenticated),('write_gate',gate),
      ('require_send_template_approval',lambda *_:None),('_binding_current',lambda *_:None),
      ('_preflight_or_close',preflight or (lambda *_:None)),('descriptor',lambda *_:SimpleNamespace()),
      ('_candidate',lambda *_:candidate)]:stack.enter_context(patch('lib.market_send_canary.'+name,value))
   yield
 def tick(self):return run(self.root,'my','page-worker-tick',canary=True,page_control=True)
 def test_old_ready_start_is_cancelled_and_start_two_sends_only_its_one_new_creator(self):
  old=self.candidate(1);did=self.prepare(old);self.restart_grant();current=control(self.store,'my')
  with self.runtime(forbid_auth=True):result=self.tick()
  self.assertEqual(result['state'],'cancelled');self.assertEqual(result['platformWrites'],0)
  self.assertEqual(self.deliveries.get(did)['state'],'cancelled');self.assertEqual(control(self.store,'my'),current)
  with self.runtime(candidate=self.candidate(2)):result=self.tick()
  self.assertEqual((result['state'],result['realSends']),('confirmed',1));self.assertEqual(len(self.sent),2)
  self.assertTrue(control(self.store,'my')['stopRequested'])
 def test_page_worker_cannot_borrow_a_standalone_ready_canary(self):
  candidate=self.candidate(1,standalone=True);did=self.prepare(candidate);before=control(self.store,'my')
  with self.runtime(forbid_auth=True):result=self.tick()
  self.assertEqual(result['state'],'waiting_canary_authorization');self.assertEqual(result['platformWrites'],0)
  self.assertEqual(self.deliveries.get(did)['state'],'ready');self.assertEqual(control(self.store,'my'),before)
  with self.assertRaisesRegex(CycleError,'canary_authorization_unbound'):
   self.deliveries.begin(did,'card',authorized_snapshot_hash=digest(candidate),recipient_verified=True,allowance_verified=True,page_control=True)
 def test_stop_start_between_preflight_and_begin_is_refused_in_the_dispatch_transaction(self):
  candidate=self.candidate(1);did=self.prepare(candidate);changes=[]
  def preflight(*_):
   if not changes:self.restart_grant();changes.append(True)
   return None
  with self.runtime(preflight=preflight):result=self.tick()
  self.assertEqual(result['state'],'cancelled');self.assertEqual(result['platformWrites'],0);self.assertEqual(self.sent,[])
  self.assertEqual([p['state'] for p in self.deliveries.get(did)['parts']],['cancelled','cancelled'])
  self.assertTrue(control(self.store,'my')['runRequested']);self.assertFalse(control(self.store,'my')['stopRequested'])
 def test_conversation_begin_also_refuses_a_replaced_page_grant(self):
  candidate=self.candidate(1,conversation=False);did=self.prepare(candidate);self.deliveries.prepare_conversation(did)
  self.restart_grant()
  with self.assertRaisesRegex(CycleError,'canary_authorization_changed'):
   self.deliveries.begin_conversation(did,digest(candidate),page_control=True)
  self.assertEqual(self.deliveries.conversation_intent(did)['state'],'ready')
 def test_old_unknown_still_uses_only_original_readback_after_a_new_start(self):
  candidate=self.candidate(1);did=self.prepare(candidate)
  original=self.deliveries.begin(did,'card',authorized_snapshot_hash=digest(candidate),recipient_verified=True,
   allowance_verified=True,page_control=True)['requestRef'];self.deliveries.unknown(did,'card');self.restart_grant()
  before=control(self.store,'my')
  def reconcile(_root,_market,deliveries,identifier):
   self.assertEqual(identifier,did);self.assertEqual(deliveries.get(did)['parts'][0]['request_ref'],original)
   return {'state':'waiting_reconciliation','deliveryId':did,'realSends':0,'platformWrites':0}
  with self.runtime(forbid_auth=True),patch('lib.market_send_canary.reconcile_delivery',reconcile):result=self.tick()
  self.assertEqual(result['state'],'waiting_reconciliation');self.assertEqual(control(self.store,'my'),before)


if __name__=='__main__':unittest.main()
