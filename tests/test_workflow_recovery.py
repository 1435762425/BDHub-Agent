import json
import sqlite3
import tempfile
import unittest
import sys
from contextlib import closing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from lib.global_screen import fingerprint,load as screen_rules
from lib.operations_workflow import create_run,finish_stage,save_setting,start_stage,status
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore,digest,encoded
from lib.workflow_recovery import resume_selected_catalog,selected_catalog_evidence,source_id
from test_operations_scheduler import NOW


class WorkflowRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.now=NOW
        self.path=self.root/'var/second-cycle.sqlite'
        with CycleStore(self.path,lambda:self.now) as store:store.plan('bjn-local-research','it')
        apply_database(self.root,'second-cycle')
        self.store=CycleStore(self.path,lambda:self.now)
        save_setting(self.store,'it','recovery-setting-001',0,{'automaticOperationsEnabled':True,'fullCatalogWeeklyEnabled':True})
        self.original=self.make_run('original-selected-run',needs_human=True,writes=3)
        self.now+=20
        self.duplicate=self.make_run('duplicate-selected-run',needs_human=False,writes=0)
        self.store.db.execute("UPDATE workflow_run SET state='needs_human',error_code='workflow_retry_requires_review' WHERE run_id=?",(self.duplicate,))
        original=self.store.db.execute('SELECT * FROM workflow_run WHERE run_id=?',(self.original,)).fetchone()
        duplicate=self.store.db.execute('SELECT * FROM workflow_run WHERE run_id=?',(self.duplicate,)).fetchone()
        self.source=source_id(original);self.other_source=source_id(duplicate);self.head='coverage-fixture'
        scope={'market':'it','account':'acc9','institutionFingerprint':'identity','source':'opportunity_global_only'}
        self.source_path=self.root/'var/global-source.sqlite'
        with closing(sqlite3.connect(self.source_path)) as db,db:
            db.executescript('CREATE TABLE global_source_run(id TEXT,state TEXT,scope TEXT,identity_unchanged INTEGER,terminal_reason TEXT);'
                             'CREATE TABLE global_source_head(run_id TEXT);CREATE TABLE global_source_product(run_id TEXT,pid TEXT);')
            db.execute('INSERT INTO global_source_run VALUES(?,?,?,?,?)',(self.source,'completed',encoded(scope),1,'endpoint_end'))
            db.execute('INSERT INTO global_source_run VALUES(?,?,?,?,?)',(self.other_source,'stopped',encoded(scope),1,'operator_stopped_duplicate_plain_collection'))
            coverage=scope|{'partitionMode':'category_l1_v1','coverageOverlay':{'refreshRunId':self.source}}
            db.execute('INSERT INTO global_source_run VALUES(?,?,?,?,?)',(self.head,'completed',encoded(coverage),1,'weekly_overlap_overlay'))
            db.execute('INSERT INTO global_source_head VALUES(?)',(self.head,))
            db.execute('INSERT INTO global_source_product VALUES(?,?)',(self.head,'1'))
        rules=screen_rules(self.root);self.intake='select-'+digest(['it',self.head,rules,fingerprint(rules),'user-300-inclusive'])[:24]
        self.selection_path=self.root/'var/global-selection.sqlite'
        with closing(sqlite3.connect(self.selection_path)) as db,db:
            db.executescript('CREATE TABLE intake_run(id TEXT,rules TEXT,source_run TEXT);CREATE TABLE intake_item(run_id TEXT,pid TEXT,state TEXT,payload TEXT,updated REAL);')
            db.execute('INSERT INTO intake_run VALUES(?,?,?)',(self.intake,encoded(rules),self.head))
            payload={'campaign':{'campaign':{'campaign_id':'123'}},'selectionEvidence':[{'campaignId':'123'}]}
            db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',(self.intake,'1','confirmed',encoded(payload),NOW))
            skipped={'receipt':{'ambiguous':True},'reason':'unresolved_after_two_delayed_readbacks',
                     'readbackAbsences':[{'at':1,'selectedPoolAbsent':True},{'at':32,'selectedPoolAbsent':True}]}
            db.execute('INSERT INTO intake_item VALUES(?,?,?,?,?)',(self.intake,'2','skipped_unknown',encoded(skipped),NOW))

    def tearDown(self):self.store.close();self.temp.cleanup()

    def make_run(self,request,*,needs_human,writes):
        run=create_run(self.store,market='it',trigger_source='schedule',scheduled_at=NOW,
                       request_id=request,sources=['selected'])
        rid=run['runId'];upstream=None
        # A same-day duplicate skips link maintenance once the original has cleaned.
        if run['stages'][0]['state']=='queued':
            start_stage(self.store,rid,'taplink_clean')
            upstream=finish_stage(self.store,rid,'taplink_clean',state='completed')['outputGenerationId']
        start_stage(self.store,rid,'catalog',input_generation_id=upstream)
        finish_stage(self.store,rid,'catalog',state='needs_human' if needs_human else 'failed',
                     platform_writes=writes,complete=False,
                     error_code='parallel_selection_requires_review' if needs_human else 'global_catalog_not_published')
        return rid

    def test_recovery_requeues_original_stage_and_preserves_unknown_and_writes(self):
        proof=selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate)
        self.assertEqual(proof['selectionStates'],{'confirmed':1,'skipped_unknown':1})
        result=resume_selected_catalog(self.store,self.root,'it',self.original,self.duplicate,'recovery-request-001')
        self.assertFalse(result['duplicate'])
        self.assertEqual(status(self.store)['current']['runId'],self.original)
        stages=result['run']['stages'];self.assertEqual(stages[1]['state'],'queued')
        self.assertEqual(stages[1]['platformWrites'],3)
        self.assertEqual(self.store.db.execute('SELECT state FROM workflow_run WHERE run_id=?',(self.duplicate,)).fetchone()[0],'stopped')
        again=resume_selected_catalog(self.store,self.root,'it',self.original,self.duplicate,'recovery-request-001')
        self.assertTrue(again['duplicate'])
        start_stage(self.store,self.original,'catalog',input_generation_id=stages[1]['inputGenerationId'])
        completed=finish_stage(self.store,self.original,'catalog',state='completed',platform_writes=0)
        self.assertEqual(completed['platformWrites'],3)
        with closing(sqlite3.connect(self.selection_path)) as db:
            self.assertEqual(db.execute("SELECT state FROM intake_item WHERE pid='2'").fetchone()[0],'skipped_unknown')

    def test_unknown_selection_or_wrong_campaign_cannot_resume(self):
        for state,payload,error in [('result_unknown',{},'workflow_selection_unresolved'),
                                   ('confirmed',{'campaign':{'campaign':{'campaign_id':'123'}},'selectionEvidence':[{'campaignId':'456'}]},'workflow_selection_receipt_unverified')]:
            with closing(sqlite3.connect(self.selection_path)) as db,db:
                db.execute("UPDATE intake_item SET state=?,payload=? WHERE pid='1'",(state,encoded(payload)))
            with self.assertRaisesRegex(CycleError,error):
                resume_selected_catalog(self.store,self.root,'it',self.original,self.duplicate,'recovery-request-001')
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM workflow_checkpoint').fetchone()[0],0)

    def test_a_confirmation_on_the_platforms_own_campaign_at_the_same_total_is_verified(self):
        rehomed={'campaign':{'campaign':{'campaign_id':'123'},'freshProduct':{'commission_rate':'1500'}},
                 'selectionEvidence':[{'pid':'1','campaignId':'456','type':9,'totalBasis':'1500','platformAssignedCampaign':True}]}
        with closing(sqlite3.connect(self.selection_path)) as db,db:
            db.execute("UPDATE intake_item SET payload=? WHERE pid='1'",(encoded(rehomed),))
        proof=selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate)
        self.assertEqual(proof['selectionStates'],{'confirmed':1,'skipped_unknown':1})
        rehomed['selectionEvidence'][0]['totalBasis']='1200'
        with closing(sqlite3.connect(self.selection_path)) as db,db:
            db.execute("UPDATE intake_item SET payload=? WHERE pid='1'",(encoded(rehomed),))
        with self.assertRaisesRegex(CycleError,'workflow_selection_receipt_unverified'):
            selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate)

    def test_isolated_unverified_selection_resumes_without_reaching_link_preparation(self):
        mismatch={'campaign':{'campaign':{'campaign_id':'123'}},'selectionEvidence':[{'campaignId':'456'}]}
        with closing(sqlite3.connect(self.selection_path)) as db,db:
            db.execute("UPDATE intake_item SET payload=? WHERE pid='1'",(encoded(mismatch),))
        with self.assertRaisesRegex(CycleError,'workflow_selection_receipt_unverified'):
            selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate)
        for wrong in (['1','2'],['1','9'],['1','1'],['x']):
            with self.subTest(isolate=wrong),self.assertRaisesRegex(CycleError,'workflow_isolation_scope_invalid'):
                selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate,wrong)
        proof=selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate,['1'])
        self.assertEqual(proof['isolated'],[{'pid':'1','frozenCampaignId':'123','evidenceCampaignIds':['456']}])
        self.assertEqual(proof['selectionStates'],{'isolated_unverified':1,'skipped_unknown':1})
        result=resume_selected_catalog(self.store,self.root,'it',self.original,self.duplicate,'recovery-request-002',['1'])
        self.assertEqual(result['run']['stages'][1]['state'],'queued')
        with closing(sqlite3.connect(self.selection_path)) as db:
            state,payload=db.execute("SELECT state,payload FROM intake_item WHERE pid='1'").fetchone()
        payload=json.loads(payload)
        self.assertEqual(state,'isolated_unverified')
        self.assertEqual((payload['selectionEvidence'],payload['isolation']['requestId']),([{'campaignId':'456'}],'recovery-request-002'))
        self.assertTrue(resume_selected_catalog(self.store,self.root,'it',self.original,self.duplicate,'recovery-request-002',['1'])['duplicate'])
        with self.assertRaisesRegex(CycleError,'workflow_recovery_request_conflict'):
            resume_selected_catalog(self.store,self.root,'it',self.original,self.duplicate,'recovery-request-002')

    def test_setting_revision_may_move_on_while_the_switches_stay_on(self):
        save_setting(self.store,'it','recovery-setting-002',1,{'automaticOperationsEnabled':False})
        save_setting(self.store,'it','recovery-setting-003',2,{'automaticOperationsEnabled':True})
        self.assertEqual(selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate)['selectionStates'],
                         {'confirmed':1,'skipped_unknown':1})
        save_setting(self.store,'it','recovery-setting-004',3,{'automaticOperationsEnabled':False})
        with self.assertRaisesRegex(CycleError,'workflow_recovery_setting_changed'):
            selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate)

    def test_changed_head_or_duplicate_with_writes_is_rejected(self):
        self.store.db.execute("UPDATE workflow_stage_run SET platform_writes=1 WHERE run_id=? AND stage='catalog'",(self.duplicate,))
        with self.assertRaisesRegex(CycleError,'workflow_recovery_state_invalid'):
            selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate)
        self.store.db.execute("UPDATE workflow_stage_run SET platform_writes=0 WHERE run_id=? AND stage='catalog'",(self.duplicate,))
        with closing(sqlite3.connect(self.source_path)) as db,db:
            db.execute('UPDATE global_source_head SET run_id=?',(self.other_source,))
        with self.assertRaisesRegex(CycleError,'workflow_published_source_unverified'):
            selected_catalog_evidence(self.store,self.root,'it',self.original,self.duplicate)


if __name__=='__main__':unittest.main()
