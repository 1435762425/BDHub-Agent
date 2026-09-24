import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from lib.kalodata_video_scan import initialize,mark_stopped
from lib.operations_workflow import create_run,finish_stage,save_setting,start_stage
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore
from lib.workflow_recovery import resume_kalodata_auth,resume_video_author_skip
from test_operations_scheduler import NOW


class KalodataStageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.now=NOW
        path=self.root/'var/second-cycle.sqlite'
        with CycleStore(path,lambda:self.now) as store:
            store.plan('bjn-local-research','it');store.plan('bjn-local-research','uk')
        apply_database(self.root,'second-cycle')
        self.store=CycleStore(path,lambda:self.now)

    def tearDown(self):self.store.close();self.temp.cleanup()

    def kalodata_run(self,market,sources):
        if market=='it':save_setting(self.store,market,'it-setting-0001',0,{'automaticOperationsEnabled':True})
        # Only IT can switch automation on without verified accounts here; the recovery ignores the trigger.
        run=create_run(self.store,market=market,trigger_source='schedule' if market=='it' else 'manual',scheduled_at=NOW,
                       request_id=f'{market}-kalodata-run',sources=sources)['runId']
        prior=None
        for stage in ('taplink_clean','catalog','taplink_prepare'):
            start_stage(self.store,run,stage,input_generation_id=prior)
            prior=finish_stage(self.store,run,stage,state='completed',item_count=1)['outputGenerationId']
        start_stage(self.store,run,'kalodata',input_generation_id=prior)
        return run

    def stop(self,run,code):
        self.now+=1
        finish_stage(self.store,run,'kalodata',state='needs_human',complete=False,error_code=code)
        self.now+=60

    def video_stop(self):
        run=self.kalodata_run('it',['selected']);self.now+=5
        generation=initialize(self.root,[{'pid':'1729000000000000001','units':1}],'2026-08-24','2026-09-22',
                              clock=lambda:self.now)['generationId']
        self.now+=5
        mark_stopped(self.root,generation,'kalodata_video_author_missing',clock=lambda:self.now)
        self.stop(run,'kalodata_video_author_missing')
        return run,generation

    def read_page(self,at):
        self.store.db.execute('INSERT INTO kalodata_video_scan_page VALUES(?,?,?,?,?,?,?,?,?,?)',
                              ('video-generation-read','1729000000000000001',int(at),1,'fingerprint',None,None,1,'[]',at))

    def stages(self,run):
        return dict(self.store.db.execute('SELECT stage,state FROM workflow_stage_run WHERE run_id=?',(run,)))

    def assert_requeued_once(self,resume,market,run):
        result=resume(self.store,market,run,'resume-kalodata-001')
        self.assertFalse(result['duplicate'])
        self.assertEqual(tuple(self.store.db.execute('SELECT state,error_code FROM workflow_run WHERE run_id=?',
                                                     (run,)).fetchone()),('running',None))
        self.assertEqual((self.stages(run)['kalodata'],self.stages(run)['oecid']),('queued','waiting_upstream'))
        self.assertTrue(resume(self.store,market,run,'resume-kalodata-001')['duplicate'])
        with self.assertRaisesRegex(CycleError,'workflow_recovery_already_requested'):
            resume(self.store,market,run,'resume-kalodata-002')
        return result['evidence']

    def test_blocked_scan_requeues_its_own_kalodata_stage_once(self):
        run,generation=self.video_stop()
        evidence=self.assert_requeued_once(resume_video_author_skip,'it',run)
        self.assertEqual(evidence['videoGenerationId'],generation)

    def test_only_it_and_only_this_error_may_resume_the_scan(self):
        run,_=self.video_stop()
        with self.assertRaisesRegex(CycleError,'workflow_recovery_scope_invalid'):
            resume_video_author_skip(self.store,'br',run,'resume-kalodata-001')
        self.store.db.execute("UPDATE workflow_stage_run SET error_code='kalodata_video_detail_invalid' "
                              "WHERE run_id=? AND stage='kalodata'",(run,))
        with self.assertRaisesRegex(CycleError,'workflow_recovery_state_invalid'):
            resume_video_author_skip(self.store,'it',run,'resume-kalodata-001')
        self.assertEqual(self.stages(run)['kalodata'],'needs_human')

    def test_scan_stopped_otherwise_or_outside_the_stage_is_not_evidence(self):
        run,_=self.video_stop()
        self.store.db.execute("UPDATE kalodata_video_generation SET state='paused_quota',"
                              "error='kalodata_daily_quota_exhausted'")
        with self.assertRaisesRegex(CycleError,'workflow_recovery_evidence_unverified'):
            resume_video_author_skip(self.store,'it',run,'resume-kalodata-001')
        self.store.db.execute("UPDATE kalodata_video_generation SET state='blocked',"
                              "error='kalodata_video_author_missing',updated_at=?",(NOW-3600,))
        with self.assertRaisesRegex(CycleError,'workflow_recovery_evidence_unverified'):
            resume_video_author_skip(self.store,'it',run,'resume-kalodata-001')
        self.assertEqual(self.stages(run)['kalodata'],'needs_human')

    def test_lapsed_login_resumes_only_after_a_later_kalodata_read(self):
        run=self.kalodata_run('uk',['campaign']);self.read_page(NOW-10)
        self.stop(run,'kalodata_auth_required')
        with self.assertRaisesRegex(CycleError,'workflow_recovery_evidence_unverified'):
            resume_kalodata_auth(self.store,'uk',run,'resume-kalodata-001')
        self.assertEqual(self.stages(run)['kalodata'],'needs_human')
        self.read_page(self.now)
        evidence=self.assert_requeued_once(resume_kalodata_auth,'uk',run)
        self.assertEqual(evidence['kalodataReadAt'],self.now)

    def test_login_resume_needs_the_login_error_and_no_other_active_run(self):
        run=self.kalodata_run('uk',['campaign']);self.stop(run,'kalodata_scope_incomplete');self.read_page(self.now)
        with self.assertRaisesRegex(CycleError,'workflow_recovery_state_invalid'):
            resume_kalodata_auth(self.store,'uk',run,'resume-kalodata-001')
        self.store.db.execute("UPDATE workflow_stage_run SET error_code='kalodata_auth_required' WHERE run_id=? AND stage='kalodata'",(run,))
        self.store.db.execute("UPDATE workflow_run SET error_code='kalodata_auth_required' WHERE run_id=?",(run,))
        other=create_run(self.store,market='uk',trigger_source='manual',scheduled_at=NOW,request_id='uk-other-run',
                         only_stage='oecid',sources=['campaign'])['runId']
        with self.assertRaisesRegex(CycleError,'workflow_recovery_claim_active'):
            resume_kalodata_auth(self.store,'uk',run,'resume-kalodata-001')
        self.assertTrue(other)


if __name__=='__main__':unittest.main()
