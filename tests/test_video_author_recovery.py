import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from lib.kalodata_video_scan import initialize,mark_stopped
from lib.operations_workflow import create_run,finish_stage,save_setting,start_stage
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore
from lib.workflow_recovery import resume_video_author_skip
from test_operations_scheduler import NOW


class VideoAuthorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.now=NOW
        path=self.root/'var/second-cycle.sqlite'
        with CycleStore(path,lambda:self.now) as store:store.plan('bjn-local-research','it')
        apply_database(self.root,'second-cycle')
        self.store=CycleStore(path,lambda:self.now)
        save_setting(self.store,'it','video-setting-001',0,{'automaticOperationsEnabled':True,'fullCatalogWeeklyEnabled':True})
        self.run=create_run(self.store,market='it',trigger_source='schedule',scheduled_at=NOW,
                            request_id='video-author-run',sources=['selected'])['runId']
        prior=None
        for stage in ('taplink_clean','catalog','taplink_prepare'):
            start_stage(self.store,self.run,stage,input_generation_id=prior)
            prior=finish_stage(self.store,self.run,stage,state='completed',item_count=1)['outputGenerationId']
        start_stage(self.store,self.run,'kalodata',input_generation_id=prior)
        self.now+=5
        self.generation=initialize(self.root,[{'pid':'1729000000000000001','units':1}],'2026-08-24','2026-09-22',
                                   clock=lambda:self.now)['generationId']
        self.now+=5
        mark_stopped(self.root,self.generation,'kalodata_video_author_missing',clock=lambda:self.now)
        self.now+=1
        finish_stage(self.store,self.run,'kalodata',state='needs_human',complete=False,
                     error_code='kalodata_video_author_missing')
        self.now+=60

    def tearDown(self):self.store.close();self.temp.cleanup()

    def stages(self):
        return dict(self.store.db.execute('SELECT stage,state FROM workflow_stage_run WHERE run_id=?',(self.run,)))

    def test_blocked_scan_requeues_its_own_kalodata_stage_once(self):
        result=resume_video_author_skip(self.store,'it',self.run,'resume-video-001')
        self.assertFalse(result['duplicate'])
        self.assertEqual(result['evidence']['videoGenerationId'],self.generation)
        self.assertEqual(self.store.db.execute('SELECT state,error_code FROM workflow_run WHERE run_id=?',
                                               (self.run,)).fetchone()[:],('running',None))
        self.assertEqual((self.stages()['kalodata'],self.stages()['oecid']),('queued','waiting_upstream'))
        self.assertTrue(resume_video_author_skip(self.store,'it',self.run,'resume-video-001')['duplicate'])
        with self.assertRaisesRegex(CycleError,'workflow_recovery_already_requested'):
            resume_video_author_skip(self.store,'it',self.run,'resume-video-002')

    def test_only_it_and_only_this_error_may_resume(self):
        with self.assertRaisesRegex(CycleError,'workflow_recovery_scope_invalid'):
            resume_video_author_skip(self.store,'br',self.run,'resume-video-001')
        self.store.db.execute("UPDATE workflow_stage_run SET error_code='kalodata_video_detail_invalid' "
                              "WHERE run_id=? AND stage='kalodata'",(self.run,))
        with self.assertRaisesRegex(CycleError,'workflow_recovery_state_invalid'):
            resume_video_author_skip(self.store,'it',self.run,'resume-video-001')
        self.assertEqual(self.stages()['kalodata'],'needs_human')

    def test_scan_stopped_otherwise_or_outside_the_stage_is_not_evidence(self):
        self.store.db.execute("UPDATE kalodata_video_generation SET state='paused_quota',"
                              "error='kalodata_daily_quota_exhausted'")
        with self.assertRaisesRegex(CycleError,'workflow_recovery_evidence_unverified'):
            resume_video_author_skip(self.store,'it',self.run,'resume-video-001')
        self.store.db.execute("UPDATE kalodata_video_generation SET state='blocked',"
                              "error='kalodata_video_author_missing',updated_at=?",(NOW-3600,))
        with self.assertRaisesRegex(CycleError,'workflow_recovery_evidence_unverified'):
            resume_video_author_skip(self.store,'it',self.run,'resume-video-001')
        self.assertEqual(self.stages()['kalodata'],'needs_human')


if __name__=='__main__':unittest.main()
