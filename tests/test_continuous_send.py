import json
import sqlite3
import tempfile
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
        (self.root/'config/send-batch.json').write_text(json.dumps({'count':500,'widen':False,
          'windowEnabled':True,'template':'standard','window':['16:30','24:00']}))
        with CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW) as store:self.plan=store.plan('bjn-local-research','it')
        apply_database(self.root,'second-cycle',clock=lambda:NOW)
        self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW);Deliveries(self.store)
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

    def test_settings_freeze_window_template_and_auto_switch(self):
        saved=mutate_control(self.store,self.root,action='save',request_id='continuous-save-0001',expected_revision=0,
          changes={'automaticEnabled':True,'window':['16:30','24:00'],'template':'brief'})
        self.assertTrue(saved['automaticEnabled']);self.assertEqual(saved['template'],'brief')
        with self.assertRaisesRegex(Exception,'request_conflict'):
            mutate_control(self.store,self.root,action='save',request_id='continuous-save-0001',expected_revision=1,
              changes={'automaticEnabled':False})

    def test_claim_snapshot_key_is_stable_and_not_a_bulk_candidate(self):
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


if __name__=='__main__':unittest.main()
