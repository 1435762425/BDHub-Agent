import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from lib.operations_workflow import create_run,finish_stage,start_stage
from lib.schema_migrations import apply_database
from lib.second_cycle import CycleError,CycleStore
from lib.workflow_recovery import resume_after_fix
from test_operations_scheduler import NOW


class ResumeAfterFixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.now=NOW
        path=self.root/'var/second-cycle.sqlite'
        with CycleStore(path,lambda:self.now) as store:
            store.plan('bjn-local-research','uk')
        apply_database(self.root,'second-cycle')
        self.store=CycleStore(path,lambda:self.now)

    def tearDown(self):self.store.close();self.temp.cleanup()

    def failed_run(self,error='market_identity_report_invalid'):
        run=create_run(self.store,market='uk',trigger_source='manual',scheduled_at=NOW,
                       request_id='uk-after-fix-run')['runId']
        prior=None
        for stage in ('taplink_clean','catalog','taplink_prepare','kalodata'):
            start_stage(self.store,run,stage,input_generation_id=prior)
            prior=finish_stage(self.store,run,stage,state='completed',item_count=1)['outputGenerationId']
        start_stage(self.store,run,'oecid',input_generation_id=prior)
        self.now+=1
        finish_stage(self.store,run,'oecid',state='failed',complete=False,error_code=error)
        self.store.db.execute("UPDATE workflow_run SET state='needs_human',error_code='workflow_retry_exhausted' "
                              "WHERE run_id=?",(run,))
        return run

    def stages(self,run):
        return dict(self.store.db.execute('SELECT stage,state FROM workflow_stage_run WHERE run_id=?',(run,)))

    def test_resumes_first_failed_stage_once(self):
        run=self.failed_run()
        result=resume_after_fix(self.store,'uk',run,'resume-after-fix-001')
        self.assertFalse(result['duplicate'])
        self.assertEqual(result['evidence']['stage'],'oecid')
        self.assertEqual(tuple(self.store.db.execute('SELECT state,error_code FROM workflow_run WHERE run_id=?',
                                                     (run,)).fetchone()),('running',None))
        self.assertEqual((self.stages(run)['oecid'],self.stages(run)['send_pool']),('queued','waiting_upstream'))
        self.assertTrue(resume_after_fix(self.store,'uk',run,'resume-after-fix-001')['duplicate'])
        with self.assertRaisesRegex(CycleError,'workflow_recovery_already_requested'):
            resume_after_fix(self.store,'uk',run,'resume-after-fix-002')

    def test_error_not_in_allowlist_is_rejected(self):
        run=self.failed_run('market_identity_report_missing')
        with self.assertRaisesRegex(CycleError,'workflow_recovery_state_invalid'):
            resume_after_fix(self.store,'uk',run,'resume-after-fix-001')
        self.assertEqual(self.stages(run)['oecid'],'failed')

    def test_stage_with_platform_writes_is_rejected(self):
        run=self.failed_run()
        self.store.db.execute("UPDATE workflow_stage_run SET platform_writes=1 WHERE run_id=? AND stage='oecid'",(run,))
        with self.assertRaisesRegex(CycleError,'workflow_recovery_state_invalid'):
            resume_after_fix(self.store,'uk',run,'resume-after-fix-001')
        self.assertEqual(self.stages(run)['oecid'],'failed')

    def test_another_active_run_of_the_market_is_rejected(self):
        run=self.failed_run()
        other=create_run(self.store,market='uk',trigger_source='manual',scheduled_at=NOW,request_id='uk-other-run',
                         only_stage='oecid')['runId']
        with self.assertRaisesRegex(CycleError,'workflow_recovery_claim_active'):
            resume_after_fix(self.store,'uk',run,'resume-after-fix-001')
        self.assertTrue(other)

    def test_wrong_market_is_rejected(self):
        run=self.failed_run()
        with self.assertRaisesRegex(CycleError,'workflow_recovery_state_invalid'):
            resume_after_fix(self.store,'it',run,'resume-after-fix-001')
        self.assertEqual(self.stages(run)['oecid'],'failed')


if __name__=='__main__':unittest.main()
