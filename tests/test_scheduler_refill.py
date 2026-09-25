"""A slow stage does not hold another market's ready work behind a batch barrier."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from unittest.mock import patch
import unittest
import test_operations_scheduler as fixtures
from lib.operations_scheduler import tick,status_path
from lib.operations_workflow import create_run,start_stage,finish_stage,save_setting
from lib.second_cycle import CycleStore


class RefillTests(unittest.TestCase):
    def setUp(self):
        fixtures.SchedulerFlow.setUp(self)
        self.clock=[fixtures.NOW]
        self.stop=threading.Event();self.release=threading.Event();self.entered=threading.Event()
        self.store.plan('bjn-local-research','uk')
        self.long=create_run(self.store,market='uk',trigger_source='manual',scheduled_at=fixtures.NOW,
                            request_id='long-platform-stage',only_stage='oecid',sources=['campaign'])
        self.patches=[patch('lib.operations_scheduler._background'),patch('lib.operations_scheduler._project_read_models')]
        for p in self.patches:p.start()
    def tearDown(self):
        self.release.set()
        for p in reversed(self.patches):p.stop()
        fixtures.SchedulerFlow.tearDown(self)
    def executor(self,other):
        parent=self
        class Executor:
            def execute(self,store,run,stage,jobs):
                if run['runId']==parent.long['runId']:
                    parent.entered.set()
                    if not parent.release.wait(3):raise AssertionError('test long stage timed out')
                else:other(store,run,stage,jobs)
                return {'state':'completed','itemCount':1,'complete':True,'platformWrites':0}
        return Executor()
    def run_tick(self,executor):
        return tick(self.root,refill=True,clock=lambda:self.clock[0],wait_seconds=.01,
                    executor=executor,stopped=self.stop.is_set)
    def wait_status(self,predicate):
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            value=json.loads(status_path(self.root).read_text())
            if predicate(value):return value
            time.sleep(.01)
        self.fail('scheduler status condition did not arrive')
    def failed_retry(self):
        save_setting(self.store,'it','retry-enabled-request',0,{'automaticOperationsEnabled':True})
        run=create_run(self.store,market='it',trigger_source='schedule',scheduled_at=fixtures.NOW,
                       request_id='scheduled-retry-original',only_stage='kalodata',sources=['campaign'])
        start_stage(self.store,run['runId'],'kalodata')
        finish_stage(self.store,run['runId'],'kalodata',state='failed',complete=False,error_code='temporary_failure')
        return run
    def test_due_retry_starts_before_unrelated_long_stage_finishes(self):
        run=self.failed_retry();self.clock[0]+=3599;started=threading.Event();calls=[]
        def other(_store,current,stage,_jobs):calls.append((current['runId'],stage));started.set()
        with ThreadPoolExecutor(max_workers=1) as worker:
            future=worker.submit(self.run_tick,self.executor(other))
            try:
                self.assertTrue(self.entered.wait(1));self.clock[0]+=2
                self.assertTrue(started.wait(1),'retry waited for the unrelated platform stage')
                self.assertFalse(future.done())
                self.assertEqual(calls,[(run['runId'],'kalodata')])
                self.wait_status(lambda s:s['checkedAt']==self.clock[0])
            finally:self.release.set()
            future.result(timeout=2)
    def test_status_and_lease_refresh_even_without_any_completion(self):
        with ThreadPoolExecutor(max_workers=1) as worker:
            future=worker.submit(self.run_tick,self.executor(lambda *_:None))
            try:
                self.assertTrue(self.entered.wait(1));self.clock[0]+=31
                state=self.wait_status(lambda s:s['checkedAt']==self.clock[0] and bool(s['runningStages']))
                with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
                    self.assertEqual(store.db.execute('SELECT lease_until FROM workflow_stage_claim').fetchone()[0],self.clock[0]+300)
                self.assertEqual(state['runningStages'][0]['market'],'uk')
            finally:self.release.set()
            future.result(timeout=2)
    def test_switch_disabled_while_waiting_prevents_the_due_retry(self):
        self.failed_retry();self.clock[0]+=3599;calls=[]
        with ThreadPoolExecutor(max_workers=1) as worker:
            future=worker.submit(self.run_tick,self.executor(lambda *args:calls.append(args)))
            try:
                self.assertTrue(self.entered.wait(1))
                save_setting(self.store,'it','disable-before-retry',1,{'automaticOperationsEnabled':False})
                self.clock[0]+=2
                self.wait_status(lambda s:s['checkedAt']==self.clock[0])
                self.assertEqual(calls,[])
            finally:self.release.set()
            future.result(timeout=2)
    def test_changed_fence_never_publishes_a_late_stage_result(self):
        with ThreadPoolExecutor(max_workers=1) as worker:
            future=worker.submit(self.run_tick,self.executor(lambda *_:None))
            try:
                self.assertTrue(self.entered.wait(1))
                with self.store.tx():
                    self.store.db.execute("UPDATE workflow_stage_claim SET fence=fence+100,owner_id='other-scheduler-owner'")
            finally:self.release.set()
            value=future.result(timeout=2)
        self.assertEqual(value['error'],'workflow_stage_fence_stale')
        stage=self.store.db.execute('SELECT state,output_generation_id FROM workflow_stage_run WHERE run_id=? AND stage=\'oecid\'',(self.long['runId'],)).fetchone()
        self.assertEqual(tuple(stage),('running',None))

    def test_stop_signal_drains_existing_claim_without_launching_due_work(self):
        self.failed_retry();self.clock[0]+=3599;calls=[]
        with ThreadPoolExecutor(max_workers=1) as worker:
            future=worker.submit(self.run_tick,self.executor(lambda *args:calls.append(args)))
            try:
                self.assertTrue(self.entered.wait(1));self.stop.set();self.clock[0]+=2
                self.wait_status(lambda s:s.get('state')=='stopping')
                self.assertEqual(calls,[])
            finally:self.release.set()
            future.result(timeout=2)
        with CycleStore(self.root/'var/second-cycle.sqlite',readonly=True) as store:
            self.assertEqual(store.db.execute('SELECT count(*) FROM workflow_stage_claim').fetchone()[0],0)


if __name__=='__main__':unittest.main()
