import json
import fcntl
import os
import sqlite3
import tempfile
import threading
import unittest
import sys
from contextlib import closing,contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import lib.continuous_send as continuous  # noqa:E402
from lib.continuous_send import control,mutate_control,publish_runtime,status  # noqa:E402
from lib.cycle_delivery import Deliveries  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleStore,digest  # noqa:E402
from lib.catalog_binding import offer_fingerprint  # noqa:E402
from lib.template_library import review_send_template,send_template_reviews  # noqa:E402

NOW=1_789_444_800.0

def candidate(creator,pid):
    return {'creatorId':creator,'pid':pid,'handle':creator,'oecId':'1'+creator,
      'offer':{'pid':pid,'offerKey':f'selected:{pid}:7','creatorPercent':'13','publicPercent':'10',
        'totalPercent':'15','campaignId':'7','catalogSource':'selected','stock':'101','available':True,
        'endAt':2_000_000_000},'offerFingerprint':'a'*64,'planRevision':1,'controlRevision':1,
      'name':{'shortNameZh':'商品','shortNameIt':'prodotto','mentionIt':'questo prodotto'},
      'nameSource':'缓存','card':{'listId':'9'+pid,'pid':pid,'sourceCampaignId':'7','wireCampaignId':'0',
        'listName':'BJN prodotto 13%','campaignName':'','checkedAt':NOW,'evidenceRefs':['proof'],
        'state':'verified_read_only','creatorPercent':'13','publicPercent':'10'},
      'identityEvidence':'identity-proof','relationshipUnlocked':False,
      'source':{'sourceId':'source-'+pid,'windowEnd':1,'units':1}}


class ContinuousSendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'var').mkdir();(self.root/'config').mkdir()
        for name in ('markets.json','market-content.json','operations-policy.json'):
            (self.root/'config'/name).write_text((ROOT/'config'/name).read_text())
        (self.root/'config/send-batch.json').write_text(json.dumps({'count':500,'widen':False,
          'windowEnabled':True,'template':'standard','window':['16:30','24:00']}))
        with CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW) as store:self.plan=store.plan('bjn-local-research','it')
        apply_database(self.root,'second-cycle',clock=lambda:NOW)
        self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW);Deliveries(self.store)
        for row in send_template_reviews(self.store,self.root)['items'][:10]:
            review_send_template(self.store,self.root,'fixture-'+row['templateId'],row['templateId'],
                                 'approved',row['revision'])
        with closing(sqlite3.connect(self.root/'var/creator-identities.sqlite')) as db,db:
            db.execute('CREATE TABLE creator_identity(creator_id,oec_id,market,current_handle,handle_conflict)')
            db.execute("INSERT INTO creator_identity VALUES('c1','1c1','it','c1',0)")
        with closing(sqlite3.connect(self.root/'var/it-conversations.sqlite')) as db,db:
            db.execute('CREATE TABLE conversation(scope,cid,oec,kind,seen)')
    def tearDown(self):self.store.close();self.temp.cleanup()

    def test_default_is_off_and_start_stop_are_revisioned_idempotent(self):
        initial=control(self.store,self.root);self.assertFalse(initial['automaticEnabled']);self.assertFalse(initial['runRequested'])
        started=mutate_control(self.store,self.root,action='start',request_id='continuous-start-0001',expected_revision=0)
        self.assertTrue(started['runRequested']);self.assertEqual(started['revision'],1)
        self.assertTrue(mutate_control(self.store,self.root,action='start',request_id='continuous-start-0001',expected_revision=0)['duplicate'])
        stopped=mutate_control(self.store,self.root,action='stop',request_id='continuous-stop-0001',expected_revision=1)
        self.assertTrue(stopped['stopRequested']);self.assertFalse(stopped['runRequested'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_bulk').fetchone()[0],0)

    def test_home_summary_keeps_runtime_without_rebuilding_the_send_candidate(self):
        with (patch.object(continuous,'_candidate',side_effect=AssertionError('candidate_must_not_run')),
              patch.object(continuous,'capacity',side_effect=AssertionError('capacity_must_not_run'))):
            result=status(self.root,self.store,include_preview=False)
        self.assertEqual(result['market'],'it')
        self.assertEqual(result['runtime']['confirmedToday'],0)
        self.assertIsNone(result['sample'])
        self.assertIsNone(result['poolRemaining'])

    def test_start_is_blocked_until_ten_semantic_templates_are_approved(self):
        with self.store.tx():
            self.store.db.execute('DELETE FROM send_template_market_review')
        with self.assertRaisesRegex(Exception,'template_approval_required'):
            mutate_control(self.store,self.root,action='start',request_id='continuous-start-blocked',expected_revision=0)

    def test_settings_freeze_window_template_and_auto_switch(self):
        saved=mutate_control(self.store,self.root,action='save',request_id='continuous-save-0001',expected_revision=0,
          changes={'automaticEnabled':True,'window':['16:30','24:00'],'template':'brief'})
        self.assertTrue(saved['automaticEnabled']);self.assertEqual(saved['template'],'brief')
        with self.assertRaisesRegex(Exception,'request_conflict'):
            mutate_control(self.store,self.root,action='save',request_id='continuous-save-0001',expected_revision=1,
              changes={'automaticEnabled':False})

    @patch('lib.continuous_send._local_card',return_value=None)
    @patch('lib.cycle_delivery.capacity_for_candidate',return_value=True)
    def test_claim_snapshot_key_is_stable_and_not_a_bulk_candidate(self,*_):
        original_pool,original_choose=continuous.pool,continuous.choose_candidates
        try:
            continuous.pool=lambda *_args,**_kwargs:{'available':True,'layers':{'ready':1},'pools':{'ready':[{'creatorId':'c1','pid':'1729480061238089885'}]}}
            base=candidate('c1','1729480061238089885');base['oecId']='1c1';base['source']['sourceId']='source-1729480061238089885'
            continuous.choose_candidates=lambda *_args,**_kwargs:([base],[])
            current=control(self.store,self.root)
            one,_=continuous._candidate(self.root,self.store,self.plan,current)
            two,_=continuous._candidate(self.root,self.store,self.plan,current)
            self.assertEqual(one['continuousClaimKey'],two['continuousClaimKey'])
            self.assertEqual(one['executionMode'],'continuous-v1')
            self.assertIn('templateRevision',one['message'])
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM cycle_bulk_candidate').fetchone()[0],0)
        finally:continuous.pool,continuous.choose_candidates=original_pool,original_choose

    @patch('lib.continuous_send._local_card',return_value=None)
    @patch('lib.cycle_delivery.capacity_for_candidate',return_value=True)
    def test_a_pool_larger_than_one_review_is_checked_from_its_top(self,*_):
        original_pool,original_choose=continuous.pool,continuous.choose_candidates
        seen=[]
        try:
            ready=[{'creatorId':f'r{n}','pid':str(1729480061238089000+n)} for n in range(600)]
            queued=[{'creatorId':f'q{n}','pid':str(1729480061238099000+n)} for n in range(2200)]
            continuous.pool=lambda *_args,**_kwargs:{'available':True,'layers':{'ready':600,'queued':2200},'pools':{'ready':ready,'queued':queued}}
            continuous.choose_candidates=lambda _s,_p,_i,limit,positions:(seen.append((limit,positions)) or ([],[]))
            continuous._candidate(self.root,self.store,self.plan,control(self.store,self.root))
            limit,positions=seen[0]
            self.assertEqual((limit,len(positions)),(continuous.REVIEW_POSITIONS,continuous.REVIEW_POSITIONS))
            self.assertEqual(positions[0],('r0',ready[0]['pid']));self.assertEqual(positions[600],('q0',queued[0]['pid']))
        finally:continuous.pool,continuous.choose_candidates=original_pool,original_choose

    @patch('lib.continuous_send._local_card',return_value=None)
    @patch('lib.cycle_delivery.capacity_for_candidate',return_value=True)
    def test_candidate_skips_every_existing_immutable_delivery(self,*_):
        original_pool,original_choose=continuous.pool,continuous.choose_candidates
        try:
            first=candidate('c1','1729480061238089885');first['oecId']='1c1'
            first['source']['sourceId']='source-1729480061238089885'
            second=candidate('c2','1729480061238089886');second['oecId']='1c2'
            second['source']['sourceId']='source-1729480061238089886'
            continuous.pool=lambda *_args,**_kwargs:{'available':True,'layers':{'ready':2},'pools':{'ready':[
              {'creatorId':'c1','pid':first['pid']},{'creatorId':'c2','pid':second['pid']}]}}
            continuous.choose_candidates=lambda *_args,**_kwargs:([first,second],[])
            with self.store.tx():
                self.store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                  ('delivery-existing',self.plan,first['creatorId'],first['oecId'],first['pid'],
                   first['source']['sourceId'],json.dumps(first),NOW,NOW+1800,'cancelled'))
            selected,_=continuous._candidate(self.root,self.store,self.plan,control(self.store,self.root))
            self.assertEqual(selected['creatorId'],'c2')
        finally:continuous.pool,continuous.choose_candidates=original_pool,original_choose

    def test_runtime_and_status_keep_unknown_distinct_from_failure(self):
        publish_runtime(self.store,self.plan,'waiting_reconciliation',
          delivery={'id':'delivery-x','creator_id':'c1','pid':'1729480061238089885'},
          stop_reason='result_unknown',unknown=1)
        snapshot=status(self.root,self.store)
        self.assertEqual(snapshot['runtime']['state'],'waiting_reconciliation')
        self.assertEqual(snapshot['runtime']['unknown'],1)
        self.assertTrue(snapshot['legacyBatchRetired'])
        self.assertEqual(snapshot['platformWrites'],0)

    def test_explicit_once_authorization_bypasses_only_the_time_window(self):
        mutate_control(self.store,self.root,action='start',request_id='continuous-start-once',expected_revision=0)
        original_window,original_candidate=continuous.window_state,continuous._candidate
        try:
            continuous.window_state=lambda *_args,**_kwargs:{'open':False}
            continuous._candidate=lambda *_args,**_kwargs:(None,{'layers':{'ready':0}})
            ordinary=continuous.execute_once(self.root,self.store)
            self.assertEqual(ordinary['state'],'waiting_window')
            authorized=continuous.execute_once(
                self.root,self.store,authorized_now='continuous-canary-request-0001')
            self.assertEqual(authorized['state'],'paused')
            self.assertEqual(authorized['stopReason'],'send_pool_empty')
            with self.assertRaisesRegex(Exception,'request_invalid'):
                continuous.execute_once(self.root,self.store,authorized_now='short')
        finally:continuous.window_state,continuous._candidate=original_window,original_candidate

    def test_reconcile_ignores_stop_and_window_but_never_claims_a_new_candidate(self):
        with patch.object(continuous,'_candidate',side_effect=AssertionError('candidate_must_not_run')), \
             patch.object(continuous,'window_state',side_effect=AssertionError('window_must_not_gate_readback')):
            result=continuous.reconcile_once(self.root,self.store)
        self.assertEqual(result['state'],'nothing_to_reconcile')
        self.assertEqual(result['platformWrites'],0)

    def test_reconcile_uses_read_only_runtime_and_cannot_dispatch_later_text(self):
        base=candidate('c1','1729480061238089885')
        base['card']['listId']='8650756273145355030'
        base.update(executionMode='continuous-v1',conversationId='88',senderBindingHash='a'*64)
        with self.store.tx():
            self.store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('delivery-unknown',self.plan,'c1',base['oecId'],base['pid'],base['source']['sourceId'],
                 json.dumps(base),NOW,NOW+1800,'unknown'))
            self.store.db.execute("INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state) VALUES('delivery-unknown','card','card-ref','unknown')")
            self.store.db.execute("INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state) VALUES('delivery-unknown','text','text-ref','ready')")
        with patch('lib.delivery_reconciliation.read_original',return_value={
                'status':'confirmed','messageId':'123','evidenceRef':'original-read'}) as read, \
             patch.object(continuous,'execute',side_effect=AssertionError('dispatch_must_not_run')), \
             patch.object(continuous,'_candidate',side_effect=AssertionError('candidate_must_not_run')):
            result=continuous.reconcile_once(self.root,self.store)
        self.assertEqual(result['state'],'partial_delivery',result)
        self.assertEqual(result['platformWrites'],0)
        self.assertEqual(read.call_args.args[3],'card')
        self.assertEqual(self.store.db.execute("SELECT state FROM cycle_delivery_part WHERE delivery_id='delivery-unknown' AND kind='text'").fetchone()[0],'cancelled')

    def test_ordinary_tick_reconciles_submitted_card_while_stopped_outside_window(self):
        base=candidate('c1','1729480061238089885')
        base.update(executionMode='continuous-v1',conversationId='88',senderBindingHash='a'*64)
        with self.store.tx():
            self.store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('delivery-stopped',self.plan,'c1',base['oecId'],base['pid'],base['source']['sourceId'],
                 json.dumps(base),NOW,NOW+1800,'unknown'))
            self.store.db.execute("INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state) VALUES('delivery-stopped','card','card-ref','unknown')")
            self.store.db.execute("INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state) VALUES('delivery-stopped','text','text-ref','ready')")
        mutate_control(self.store,self.root,action='stop',request_id='continuous-stop-readback',expected_revision=0)
        before=control(self.store,self.root)
        with patch('lib.delivery_reconciliation.read_original',return_value={
                'status':'confirmed','messageId':'123','evidenceRef':'original-read'}) as read, \
             patch.object(continuous,'window_state',side_effect=AssertionError('readback_must_ignore_window')), \
             patch.object(continuous,'execute',side_effect=AssertionError('dispatch_must_not_run')), \
             patch.object(continuous,'_candidate',side_effect=AssertionError('candidate_must_not_run')):
            result=continuous.execute_once(self.root,self.store)
        self.assertEqual(result['stopReason'],'partial_delivery')
        read.assert_called_once()
        self.assertEqual(control(self.store,self.root),before)
        self.assertEqual([r[0] for r in self.store.db.execute(
            "SELECT state FROM cycle_delivery_part WHERE delivery_id='delivery-stopped' ORDER BY kind")],
            ['confirmed','cancelled'])

    def test_stopped_unsubmitted_delivery_is_not_advanced_by_recovery(self):
        base=candidate('c1','1729480061238089885');base['executionMode']='continuous-v1'
        with self.store.tx():
            self.store.db.execute('INSERT INTO cycle_delivery VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('delivery-ready',self.plan,'c1',base['oecId'],base['pid'],base['source']['sourceId'],
                 json.dumps(base),NOW,NOW+1800,'ready'))
            for kind in ('card','text'):
                self.store.db.execute('INSERT INTO cycle_delivery_part(delivery_id,kind,request_ref,state) VALUES(?,?,?,?)',
                    ('delivery-ready',kind,kind+'-ref','ready'))
        with patch.object(continuous,'reconcile_delivery',side_effect=AssertionError('unsubmitted_must_not_read')), \
             patch.object(continuous,'execute',side_effect=AssertionError('unsubmitted_must_not_dispatch')):
            result=continuous.execute_once(self.root,self.store)
        self.assertEqual(result['state'],'off')
        self.assertEqual(self.store.db.execute("SELECT state FROM cycle_delivery WHERE id='delivery-ready'").fetchone()[0],'ready')

    def test_paused_plan_allows_only_original_intent_reconciliation(self):
        self.store.db.execute("UPDATE plan SET state='paused' WHERE id=?",(self.plan,))
        with patch.object(continuous,'_reconcile_delivery',return_value={'id':'original-submission'}), \
             patch.object(continuous,'reconciliation_target',return_value='card'), \
             patch.object(continuous,'reconcile_delivery',return_value={'state':'quarantined_unknown','platformWrites':0,'realSends':0}) as read, \
             patch.object(continuous,'_candidate',side_effect=AssertionError('paused_plan_must_not_claim')):
            self.assertEqual(continuous.reconcile_once(self.root,self.store)['state'],'quarantined_unknown')
        read.assert_called_once()
        self.assertEqual(self.store._plan(self.plan)['state'],'paused')
        with self.assertRaisesRegex(continuous.CycleError,'plan_paused'):
            continuous.execute_once(self.root,self.store)

    def test_it_worker_preserves_only_the_readback_loop_after_manual_stop(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('continuous_worker_recovery',ROOT/'scripts/continuous-send-worker.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        before=control(self.store,self.root)
        with patch.object(worker,'ROOT',self.root), \
             patch.object(worker,'CycleStore') as store_type, \
             patch.object(worker,'control',return_value=before|{'stopRequested':True}), \
             patch.object(worker,'has_pending_reconciliation',return_value=True), \
             patch.object(worker,'execute_once',return_value={'state':'waiting_reconciliation'}) as execute, \
             patch.object(worker,'publish_runtime',return_value={'state':'waiting_reconciliation'}), \
             patch.object(worker,'print'), \
             patch('sys.argv',['continuous-send-worker.py','--once']):
            store_type.return_value.__enter__.return_value=self.store
            self.assertEqual(worker.main(),0)
        execute.assert_called_once_with(self.root,self.store,reconcile_only=True)
        self.assertEqual(control(self.store,self.root),before)

    def test_launch_observation_cannot_consume_the_persistent_stop_or_grant_sending(self):
        mutate_control(self.store,self.root,action='stop',request_id='continuous-stop-launch-test',expected_revision=0)
        stopped=control(self.store,self.root)
        with patch.object(continuous.subprocess,'Popen',return_value=SimpleNamespace(pid=os.getpid())):
            self.assertEqual(continuous.launch_worker(self.root),os.getpid())
        self.assertEqual(control(self.store,self.root),stopped)
        started=mutate_control(self.store,self.root,action='start',request_id='continuous-page-start-test',expected_revision=1)
        self.assertFalse(started['stopRequested']);self.assertTrue(started['runRequested'])

    def test_live_history_uses_current_cooldown_not_the_retired_32_day_canary_rule(self):
        base={'identityVerified':True,'hasMore':False,'senderCounts':{
          'ourMessages':1,'creatorReplies':1,'showcaseNotifications':1,'otherOrUnknown':0},
          'outboundCreateTimeRaw':[int((NOW-259200)*1000)],'outboundTimeMissingCount':0}
        continuous._continuous_history_eligible(base,NOW,True)
        with self.assertRaisesRegex(Exception,'recent_contact'):
            continuous._continuous_history_eligible(
              base|{'outboundCreateTimeRaw':[int((NOW-3600)*1000)]},NOW,True)
        with self.assertRaisesRegex(Exception,'unknown_message'):
            continuous._continuous_history_eligible(
              base|{'senderCounts':base['senderCounts']|{'otherOrUnknown':1}},NOW,True)

    def test_live_history_72h_boundary_and_own_card_exception(self):
        for unlocked in (False, True):
            for age in (259199, 259200, 259201):
                history={'identityVerified':True,'hasMore':False,
                  'senderCounts':{'ourMessages':1,'otherOrUnknown':0},
                  'outboundCreateTimeRaw':[int((NOW-age)*1000)],'outboundTimeMissingCount':0}
                with self.subTest(unlocked=unlocked,age=age):
                    if age<259200:
                        with self.assertRaisesRegex(Exception,'recent_contact'):
                            continuous._continuous_history_eligible(history,NOW,unlocked)
                    else:
                        continuous._continuous_history_eligible(history,NOW,unlocked)
            history['outboundCreateTimeRaw']=[int(NOW*1000)]
            continuous._continuous_history_eligible(history,NOW,unlocked,own_current_refs=1)
            history['senderCounts']['ourMessages']=2
            history['outboundCreateTimeRaw'].append(int((NOW-1)*1000))
            with self.assertRaisesRegex(Exception,'recent_contact'):
                continuous._continuous_history_eligible(history,NOW,unlocked,own_current_refs=1)

    def test_local_card_checks_both_offer_and_material_fingerprints_in_their_own_domains(self):
        current=candidate('c1','1729480061238089885')
        current['card']['listId']='8650756273145355030'
        current['offerFingerprint']=digest(current['offer'])
        original=self.store._offers;self.store._offers=lambda _plan:[('catalog',current['offer'])]
        try:
            with closing(sqlite3.connect(self.root/'var/catalog-links.sqlite')) as db,db:
                db.execute('''CREATE TABLE catalog_current_binding(
                  market TEXT,catalog_source TEXT,pid TEXT,campaign_id TEXT,offer_fingerprint TEXT,
                  list_id TEXT,state TEXT)''')
                db.execute('INSERT INTO catalog_current_binding VALUES(?,?,?,?,?,?,?)',
                  ('it','selected',current['pid'],'7',offer_fingerprint(current['offer']),
                   current['card']['listId'],'active'))
            card=continuous._local_card(self.root,self.store,self.plan,current)
            self.assertEqual(card.list_id,current['card']['listId'])
            self.assertNotEqual(current['offerFingerprint'],offer_fingerprint(current['offer']))
        finally:self.store._offers=original

    def test_authenticated_send_reuses_the_capability_report_in_live_runtime(self):
        mutate_control(self.store,self.root,action='start',request_id='continuous-start-report',expected_revision=0)
        base=candidate('c1','1729480061238089885');base['card']['listId']='8650756273145355030'
        base['conversationId']='88';base['authorizedNowRequestId']='continuous-canary-report-0001'
        base['continuousControlRevision']=1;base['executionMode']='continuous-v1'
        from lib.cycle_send_runtime import descriptor
        card=descriptor(base['card']);seen={}
        class FakeDeliveries:
            snapshot=None
            def __init__(self,_store):pass
            def cancel_expired_unsubmitted(self,_plan,_modes):return []
            def quarantine_refused_creates(self,_plan,_modes):return []
            def prepare(self,_plan,value):
                FakeDeliveries.snapshot=value
                return {'id':'delivery-test','creator_id':value['creatorId'],'pid':value['pid'],'state':'ready'}
            def get(self,_id):return {'id':'delivery-test','state':'ready','snapshot':FakeDeliveries.snapshot,'parts':[]}
        @contextmanager
        def authenticated(report,**_kwargs):
            report['sendCapability']='canary';seen['report']=report
            yield SimpleNamespace(),SimpleNamespace(),{},SimpleNamespace(),lambda:False,lambda:None
        @contextmanager
        def live(_binding,report,**_kwargs):
            self.assertIs(report,seen['report'])
            yield {'reads':SimpleNamespace(),'write_gate':lambda:None,'adapter':SimpleNamespace(),'card':card}
        def execute(_deliveries,_id,runtime,authorize,_preflight,**_kwargs):
            try:
                authorize(FakeDeliveries.snapshot)
                with runtime(FakeDeliveries.snapshot):pass
                return {'state':'confirmed'}
            except BaseException as error:
                seen['error']=repr(error);raise
        with patch.object(continuous,'window_state',return_value={'open':False}), \
             patch.object(continuous,'_candidate',return_value=(base,{'layers':{'ready':1}})), \
             patch.object(continuous,'_local_card',return_value=card), \
             patch.object(continuous,'Deliveries',FakeDeliveries), \
             patch.object(continuous,'execute',side_effect=execute), \
             patch.object(continuous,'publish_runtime',side_effect=lambda _store,_plan,state,**kwargs:{'state':state,**kwargs}), \
             patch('lib.second_live_runtime._authenticated',side_effect=authenticated), \
             patch('lib.second_live_runtime.live_runtime',side_effect=live), \
             patch('lib.second_live_runtime.sender_binding_sha256',return_value='a'*64):
            result=continuous.execute_once(
                self.root,self.store,authorized_now='continuous-canary-report-0001')
        self.assertEqual(result['state'],'sending',{'result':result,'seen':seen})


class ContinuousWorkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'var').mkdir()

    def worker(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('continuous_worker_lifecycle',ROOT/'scripts/continuous-send-worker.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        return worker

    def test_parallel_launches_spawn_once_without_a_shared_temporary_file_race(self):
        barrier=threading.Barrier(2);results=[];errors=[]
        def launch():
            try:barrier.wait(timeout=3);results.append(continuous.launch_worker(self.root))
            except BaseException as error:errors.append(error)
        with patch.object(continuous.subprocess,'Popen',return_value=SimpleNamespace(pid=os.getpid())) as spawn, \
             patch('lib.process_liveness.record_alive',side_effect=lambda value:value.get('pid')==os.getpid()):
            threads=[threading.Thread(target=launch) for _ in range(2)]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=5)
        self.assertFalse(errors);self.assertTrue(all(not thread.is_alive() for thread in threads))
        spawn.assert_called_once();self.assertEqual(results,[os.getpid(),os.getpid()])
        self.assertFalse((self.root/'var/continuous-send-worker.tmp').exists())

    def test_held_runtime_lock_prevents_spawning_or_overwriting_a_missing_identity(self):
        with (self.root/'var/continuous-send.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(continuous.subprocess,'Popen') as spawn:
                self.assertIsNone(continuous.launch_worker(self.root))
        spawn.assert_not_called()
        self.assertFalse((self.root/'var/continuous-send-worker.json').exists())

    def test_fast_child_heartbeat_is_not_overwritten_by_the_parent_start_record(self):
        children=[];entered=threading.Event()
        observation={'pid':os.getpid(),'startedAt':123,'checkedAt':456,'running':True}
        def heartbeat():
            entered.set();continuous.write_worker_state(self.root,observation)
        def spawn(*_args,**_kwargs):
            child=threading.Thread(target=heartbeat);child.start();children.append(child)
            self.assertTrue(entered.wait(timeout=2));child.join(timeout=.05)
            return SimpleNamespace(pid=os.getpid())
        with patch.object(continuous.subprocess,'Popen',side_effect=spawn):continuous.launch_worker(self.root)
        for child in children:child.join(timeout=5)
        self.assertTrue(all(not child.is_alive() for child in children))
        self.assertEqual(json.loads((self.root/'var/continuous-send-worker.json').read_text()),observation)

    def test_duplicate_child_checks_the_runtime_lock_before_opening_any_store(self):
        worker=self.worker()
        with (self.root/'var/continuous-send.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(worker,'ROOT',self.root),patch.object(worker,'CycleStore') as store, \
                 patch('sys.argv',['continuous-send-worker.py','--once']):
                with self.assertRaises(BlockingIOError):worker.main()
        store.assert_not_called()
        self.assertFalse((self.root/'var/continuous-send-worker.json').exists())

    def test_child_publishes_a_terminal_process_observation_when_store_open_fails(self):
        worker=self.worker()
        with patch.object(worker,'ROOT',self.root), \
             patch.object(worker,'CycleStore',side_effect=RuntimeError('fixture-store-failure')), \
             patch('sys.argv',['continuous-send-worker.py','--once']):
            with self.assertRaisesRegex(RuntimeError,'fixture-store-failure'):worker.main()
        value=json.loads((self.root/'var/continuous-send-worker.json').read_text())
        self.assertFalse(value['running']);self.assertEqual(value['pid'],os.getpid())
        self.assertGreaterEqual(value['checkedAt'],value['startedAt'])
        self.assertFalse(continuous.worker_state(self.root)['running'])


if __name__=='__main__':unittest.main()
