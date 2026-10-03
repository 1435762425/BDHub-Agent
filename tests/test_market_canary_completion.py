import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.account_identity import current_generation,publish_generation
from lib.cycle_delivery import Deliveries
from lib.market_send_canary import run
from lib.market_send_control import control,mutate,page_start_authorization,settle_confirmed_canaries
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleStore,encoded


class MarketCanaryCompletionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1700000000
  path=self.root/'var/second-cycle.sqlite'
  with CycleStore(path) as store:self.plan=store.plan('bjn-local-research','my')
  apply_database(self.root,'second-cycle')
  self.store=CycleStore(path,lambda:self.now);self.deliveries=Deliveries(self.store)
  publish_generation(self.store,market='my',account='acc8',role='communications',reason='baseline',
   identity={'browserRef':'browser','httpRef':'http','imRef':'im','institutionFingerprint':'institution'},
   capabilities={'im_identity':{'state':'verified'},'message_send':{'state':'not_tested'}})
  self.change('start','page-first-start')
  self.snapshot={'executionMode':'market-canary-v1','senderAccount':'acc8','senderImId':'123',
                 'pageStart':page_start_authorization(self.store,'my')}
  self.did='delivery-test-canary'
  self.store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
   (self.did,self.plan,'creator','101','201','source',encoded(self.snapshot),self.now,self.now+1800,'unknown'))
  for kind,state in [('card','confirmed'),('text','unknown')]:
   self.store.db.execute('INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state,started) VALUES(?,?,?,?,?)',
    (self.did,kind,'request-'+kind,state,self.now))
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def change(self,action,request):
  self.now+=1
  with patch('lib.market_send_control.require_send_template_approval'):
   return mutate(self.store,ROOT,'my',action=action,request_id=request,
                 expected_revision=control(self.store,'my')['revision'])
 def confirm(self,deliveries=None):
  (deliveries or self.deliveries).confirm(self.did,'text',{'status':'confirmed','requestRef':'request-text',
   'oecId':'101','kind':'text','messageId':'301','evidenceRef':'exact-original-receipt'})
 def generations(self):return self.store.db.execute('SELECT count(*) FROM account_identity_generation').fetchone()[0]
 def test_confirmation_consumes_one_grant_and_publication_is_idempotent(self):
  self.confirm()
  stopped=control(self.store,'my');self.assertTrue(stopped['stopRequested']);self.assertFalse(stopped['runRequested'])
  self.assertEqual(current_generation(self.store,'my','acc8')['capabilities']['message_send']['state'],'not_tested')
  result=settle_confirmed_canaries(self.store,'my','acc8')
  self.assertTrue(result[0]['canaryStopRequested'])
  self.assertEqual(current_generation(self.store,'my','acc8')['capabilities']['message_send']['state'],'verified')
  count=self.generations()
  self.assertEqual(settle_confirmed_canaries(self.store,'my','acc8'),[])
  self.confirm()
  self.assertEqual(self.generations(),count);self.assertEqual(control(self.store,'my'),stopped)
 def test_confirmation_and_stop_rollback_together(self):
  self.store.db.execute("CREATE TEMP TRIGGER fail_consumption BEFORE INSERT ON continuous_send_control_request WHEN NEW.request_id LIKE 'market-canary-stop-%' BEGIN SELECT RAISE(ABORT,'injected_crash'); END")
  with self.assertRaisesRegex(sqlite3.IntegrityError,'injected_crash'):self.confirm()
  self.assertEqual(self.deliveries.get(self.did)['state'],'unknown')
  self.assertTrue(control(self.store,'my')['runRequested'])
  self.store.db.execute('DROP TRIGGER fail_consumption');self.confirm()
  self.assertTrue(control(self.store,'my')['stopRequested'])
 def test_crash_after_publication_replays_without_a_second_generation(self):
  self.confirm()
  from lib.account_identity import promote_capabilities
  def publish_then_crash(*args,**kwargs):
   promote_capabilities(*args,**kwargs);raise RuntimeError('injected_crash')
  with patch('lib.account_identity.promote_capabilities',side_effect=publish_then_crash):
   with self.assertRaisesRegex(RuntimeError,'injected_crash'):settle_confirmed_canaries(self.store,'my','acc8')
  self.assertTrue(control(self.store,'my')['stopRequested']);count=self.generations()
  self.assertEqual(settle_confirmed_canaries(self.store,'my','acc8'),[])
  self.assertEqual(self.generations(),count)
 def test_manual_pause_is_preserved_without_revising_its_control(self):
  stopped=self.change('stop','manual-pause-first')
  self.confirm();settle_confirmed_canaries(self.store,'my','acc8')
  self.assertEqual(control(self.store,'my')['revision'],stopped['revision'])
  self.assertTrue(control(self.store,'my')['stopRequested'])
 def test_paused_plan_still_records_exact_confirmation_without_resuming(self):
  self.store.control(self.plan,'operator-plan-pause',1,'paused')
  self.confirm()
  self.assertEqual(self.deliveries.get(self.did)['state'],'confirmed')
  self.assertEqual(self.store._plan(self.plan)['state'],'paused')
  self.assertEqual(tuple(self.store.db.execute('SELECT run_requested,stop_requested FROM continuous_send_control WHERE plan_id=?',
                                               (self.plan,)).fetchone()),(0,1))
 def test_consumption_preserves_the_saved_automatic_switch(self):
  self.store.db.execute('UPDATE continuous_send_control SET automatic_enabled=1 WHERE plan_id=?',(self.plan,))
  self.confirm()
  self.assertTrue(control(self.store,'my')['automaticEnabled'])
  self.assertTrue(control(self.store,'my')['stopRequested'])
 def test_inactive_old_grant_is_not_changed_even_without_a_stop_flag(self):
  self.store.db.execute('UPDATE continuous_send_control SET run_requested=0,stop_requested=0 WHERE plan_id=?',(self.plan,))
  before=control(self.store,'my');self.confirm();settle_confirmed_canaries(self.store,'my','acc8')
  self.assertEqual(control(self.store,'my'),before)
  self.assertIsNone(self.store.db.execute('SELECT 1 FROM continuous_send_control_request WHERE request_id=?',
                                         ('market-canary-stop-'+self.did,)).fetchone())
 def test_explicit_standalone_canary_cannot_borrow_an_older_page_start(self):
  did='delivery-standalone';snapshot={**self.snapshot,'pageStart':None}
  self.store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
   (did,self.plan,'creator-standalone','102','202','source-standalone',encoded(snapshot),self.now+1,self.now+1800,'unknown'))
  for kind,state in [('card','confirmed'),('text','unknown')]:
   self.store.db.execute('INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state,started) VALUES(?,?,?,?,?)',
    (did,kind,'standalone-'+kind,state,self.now+1))
  before=control(self.store,'my')
  self.deliveries.confirm(did,'text',{'status':'confirmed','requestRef':'standalone-text','oecId':'102',
   'kind':'text','messageId':'302','evidenceRef':'standalone-receipt'})
  changes=self.store.db.total_changes
  self.assertEqual(settle_confirmed_canaries(self.store,'my','acc8'),[])
  self.assertEqual(self.store.db.total_changes,changes)
  self.assertEqual(control(self.store,'my'),before)
 def test_new_page_start_survives_replay_of_a_completed_canary(self):
  self.confirm();settle_confirmed_canaries(self.store,'my','acc8')
  resumed=self.change('start','page-resume-after-canary')
  self.assertEqual(settle_confirmed_canaries(self.store,'my','acc8'),[])
  self.assertTrue(control(self.store,'my')['runRequested'])
  self.assertEqual(control(self.store,'my')['revision'],resumed['revision'])
 def legacy_confirmed(self):
  # Simulate an older worker crashing after confirmation but before stopping/promotion.
  self.store.db.execute('DROP TRIGGER cycle_delivery_frozen')
  self.store.db.execute('UPDATE cycle_delivery SET snapshot=?,state=\'confirmed\' WHERE id=?',
   (encoded({k:v for k,v in self.snapshot.items() if k!='pageStart'}),self.did))
  self.store.db.execute("UPDATE cycle_delivery_part SET state='confirmed' WHERE delivery_id=?",(self.did,))
 def test_legacy_confirmed_restarts_before_any_candidate_or_platform_access(self):
  self.legacy_confirmed()
  before=control(self.store,'my');generations=self.generations()
  with patch('lib.market_send_canary.load_config',return_value={'markets':{'my':{'roles':{'communications':'acc8'}}}}), \
       patch('lib.market_send_canary.authenticated',side_effect=AssertionError('platform_must_not_open')):
   result=run(self.root,'my','restart-first-send',canary=False,page_control=True)
  self.assertEqual((result['state'],result['realSends'],result['platformWrites']),('waiting_canary_authorization',0,0))
  self.assertEqual(control(self.store,'my'),before);self.assertEqual(self.generations(),generations)
 def test_new_explicit_start_is_not_cancelled_by_legacy_history(self):
  self.legacy_confirmed();resumed=self.change('start','page-resume-legacy')
  before=self.store.db.total_changes
  result=settle_confirmed_canaries(self.store,'my','acc8')
  self.assertEqual(result,[]);self.assertTrue(control(self.store,'my')['runRequested'])
  self.assertEqual(control(self.store,'my')['revision'],resumed['revision'])
  self.assertEqual(self.store.db.total_changes,before)
 def test_unrecognized_frozen_start_is_not_treated_as_a_new_grant(self):
  self.legacy_confirmed()
  self.store.db.execute('UPDATE cycle_delivery SET snapshot=? WHERE id=?',
   (encoded({**self.snapshot,'pageStart':{'requestId':'missing-page-start','revision':999}}),self.did))
  before=control(self.store,'my')
  from lib.market_send_control import unbound_confirmed_canary
  self.assertEqual(settle_confirmed_canaries(self.store,'my','acc8'),[])
  self.assertEqual(unbound_confirmed_canary(self.store,'my','acc8')['state'],'waiting_canary_authorization')
  self.assertEqual(control(self.store,'my'),before)
 def test_existing_stop_and_verified_capability_need_no_historical_control_backfill(self):
  self.confirm();settle_confirmed_canaries(self.store,'my','acc8')
  before=self.store.db.total_changes
  self.assertEqual(settle_confirmed_canaries(self.store,'my','acc8'),[])
  self.assertEqual(self.store.db.total_changes,before)
  self.assertEqual(self.store.db.execute("SELECT count(*) FROM control_event WHERE event_id LIKE 'market-canary-%'").fetchone()[0],0)
 def test_verified_continuous_path_does_not_scan_delivery_history(self):
  self.confirm();settle_confirmed_canaries(self.store,'my','acc8');queries=[]
  self.store.db.set_trace_callback(queries.append)
  self.assertEqual(settle_confirmed_canaries(self.store,'my','acc8'),[])
  self.store.db.set_trace_callback(None)
  self.assertFalse(any('cycle_delivery' in q.lower() or 'begin' in q.lower() for q in queries))
 def test_exact_legacy_stop_receipt_can_finish_capability_without_changing_controls(self):
  self.confirm();self.legacy_confirmed();before=control(self.store,'my')
  self.assertTrue(settle_confirmed_canaries(self.store,'my','acc8')[0]['canaryCompleted'])
  self.assertEqual(control(self.store,'my'),before)
 def test_read_only_recovery_confirmation_stops_and_promotes_without_a_new_send(self):
  def reconcile(_root,_market,deliveries,did):
   self.assertEqual(did,self.did);self.confirm(deliveries)
   return {'state':'confirmed','deliveryId':did,'realSends':0,'platformWrites':0}
  with patch('lib.market_send_canary.load_config',return_value={'markets':{'my':{'roles':{'communications':'acc8'}}}}), \
       patch('lib.market_send_canary.reconcile_delivery',side_effect=reconcile), \
       patch('lib.market_send_canary.authenticated',side_effect=AssertionError('send_must_not_open')):
   result=run(self.root,'my','recover-first-send',page_control=True,reconcile_only=True)
  self.assertTrue(result['canaryCompleted']);self.assertTrue(result['canaryStopRequested'])
  self.assertEqual((result['realSends'],result['platformWrites']),(0,0))
  self.assertEqual(current_generation(self.store,'my','acc8')['capabilities']['message_send']['state'],'verified')


if __name__=='__main__':unittest.main()
