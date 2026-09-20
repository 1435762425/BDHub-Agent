import json
import os
import tempfile
import unittest
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.jobs import save  # noqa:E402
from lib.operations_scheduler import (SubprocessStageExecutor,due_slot,scheduler_state,start_scheduler,
                                      stop_scheduler,tick)  # noqa:E402
from lib.operations_workflow import save_setting,status as workflow_status  # noqa:E402
from lib.schema_migrations import apply_database  # noqa:E402
from lib.second_cycle import CycleStore  # noqa:E402

TZ=ZoneInfo('Asia/Shanghai')
NOW=datetime(2026,9,21,7,5,tzinfo=TZ).timestamp()


class FakeExecutor:
    def __init__(self):self.calls=[]
    def execute(self,store,run,stage,jobs):
        self.calls.append((run['runId'],stage))
        return {'state':'completed','itemCount':len(self.calls),'complete':True,'platformWrites':0,
                'scope':{'stage':stage},'payload':{'handled':len(self.calls)}}


class SchedulerFlow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'var').mkdir();(self.root/'config').mkdir()
        with CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW) as store:store.plan('bjn-local-research','it')
        apply_database(self.root,'second-cycle',clock=lambda:NOW)
        self.store=CycleStore(self.root/'var/second-cycle.sqlite',lambda:NOW)
    def tearDown(self):self.store.close();self.temp.cleanup()

    def test_every_switch_off_means_no_run(self):
        save(self.root,{'jobs':{}});executor=FakeExecutor();state=tick(self.root,now=NOW,executor=executor)
        self.assertIsNone(state['runId']);self.assertEqual(executor.calls,[])

    def test_master_switch_alone_runs_the_monday_chain_in_order(self):
        save(self.root,{'jobs':{}})
        save_setting(self.store,'it','automation-setting-request',0,
                     {'automaticOperationsEnabled':True,'fullCatalogWeeklyEnabled':True})
        executor=FakeExecutor()
        for _ in range(6):tick(self.root,now=NOW,executor=executor)
        self.assertEqual([stage for _,stage in executor.calls],
                         ['taplink_clean','catalog','taplink_prepare','kalodata','oecid','send_pool'])
        current=workflow_status(self.store)['current'];self.assertEqual(current['state'],'completed')
        self.assertTrue(all(stage['outputGenerationId'] for stage in current['stages']))

    def test_daily_due_uses_0700_and_monday_clean_uses_0430(self):
        save(self.root,{'jobs':{'taplink_clean':{'enabled':False,'at':'04:30','weekday':0},
                               'campaign_catalog_update':{'enabled':False,'at':'07:00'}}})
        base={'fullCatalogWeeklyEnabled':False}
        self.assertEqual(datetime.fromtimestamp(due_slot(self.root,NOW,base),TZ).strftime('%H:%M'),'04:30')
        self.assertEqual(datetime.fromtimestamp(due_slot(self.root,NOW+86400,base),TZ).strftime('%H:%M'),'07:00')


class StageWiring(unittest.TestCase):
    def executor(self,answers):
        executor=SubprocessStageExecutor(ROOT);calls=[]
        def call(args,label,timeout=14400):
            calls.append((args,label))
            return answers(args,label)
        executor._call=call
        return executor,calls

    def test_taplink_prepares_every_short_name_before_any_create(self):
        def answers(args,_label):
            payload={'missing':0} if args[0]=='scripts/catalog-names.py' else {}
            return {'state':'completed','itemCount':1,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'applicableSources':['campaign']},'taplink_prepare',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        self.assertEqual([args[0] for args,_ in calls],
                         ['scripts/catalog-link-batch.py','scripts/catalog-names.py','scripts/catalog-link-batch.py'])
        self.assertEqual(calls[0][0][calls[0][0].index('--creates')+1],'0')
        self.assertEqual(calls[2][0][calls[2][0].index('--creates')+1],'200')

    def test_campaign_joins_before_collecting_the_new_joined_catalog(self):
        def answers(args,_label):
            if args[1]=='status':payload={'available':False}
            elif args[1]=='join-all':payload={'state':'completed','unresolved':[]}
            else:payload={'status':'completed','screening':{'recorded':True},'offers':7}
            return {'state':'completed','itemCount':0,'complete':True,
                    'platformWrites':2 if args[1]=='join-all' else 0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'applicableSources':['campaign']},'catalog',{'jobs':{}})
        self.assertEqual(result['state'],'completed');self.assertEqual(result['platformWrites'],2)
        self.assertEqual([args[:2] for args,_ in calls],[['scripts/campaign-join.py','status'],
                         ['scripts/campaign-join.py','join-all'],['scripts/campaign-collect.py','--max-requests']])


class FakeChild:pid=os.getpid()
class Spawn:
    def __call__(self,*args,**kwargs):return FakeChild()


class SchedulerControl(unittest.TestCase):
    def test_start_and_stop_touch_only_local_scheduler_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);started=start_scheduler(root,clock=lambda:100,spawn=Spawn())
            self.assertTrue(started['running']);self.assertFalse(started['stopping'])
            self.assertTrue(stop_scheduler(root)['stopping'])
            with self.assertRaisesRegex(ValueError,'already_running'):start_scheduler(root,spawn=Spawn())


if __name__=='__main__':unittest.main()
