import json
import sqlite3
import tempfile
import unittest
import sys
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import lib.continuous_send as continuous  # noqa:E402
from lib.continuous_send import control,mutate_control,publish_runtime,status  # noqa:E402
from lib.cycle_delivery import Deliveries  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402

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


if __name__=='__main__':unittest.main()
