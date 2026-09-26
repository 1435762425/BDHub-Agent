import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
import sys
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lib.jobs import save  # noqa:E402
from lib.operations_scheduler import (SubprocessStageExecutor,_scheduled_sources,accepted_partial_selection,due_slot,scheduler_state,selection_auth_unknown,start_scheduler,
                                      stop_scheduler,tick)  # noqa:E402
from lib.operations_policy import full_catalog_collection_mode,load_policy  # noqa:E402
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
        (self.root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
        (self.root/'config/markets.json').write_text((ROOT/'config/markets.json').read_text())
        accounts=json.loads((ROOT/'config/market-accounts.json').read_text())
        for pair in accounts['markets'].values():pair.update(imSessionMode='http_polling',identityAccountRole='communications')
        (self.root/'config/market-accounts.json').write_text(json.dumps(accounts))
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
        base={'fullCatalogWeeklyEnabled':True}
        self.assertEqual(datetime.fromtimestamp(due_slot(self.root,NOW,base),TZ).strftime('%H:%M'),'04:30')
        self.assertEqual(datetime.fromtimestamp(due_slot(self.root,NOW+86400,base),TZ).strftime('%H:%M'),'07:00')

    def test_campaign_source_is_due_every_two_days_not_every_day(self):
        save(self.root,{'jobs':{'taplink_clean':{'enabled':False,'at':'04:30','weekday':0},
                               'campaign_catalog_update':{'enabled':False,'at':'07:00'}}})
        policy=load_policy(self.root);automation={'fullCatalogWeeklyEnabled':False}
        sources,_=_scheduled_sources(self.root,self.store,'it',NOW,automation,policy)
        self.assertEqual(sources,['campaign'])
        run=__import__('lib.operations_workflow',fromlist=['create_run']).create_run(
            self.store,market='it',trigger_source='manual',scheduled_at=NOW,sources=['campaign'])
        from lib.operations_workflow import start_stage,finish_stage
        start_stage(self.store,run['runId'],'taplink_clean');finish_stage(self.store,run['runId'],'taplink_clean',state='skipped')
        start_stage(self.store,run['runId'],'catalog');finish_stage(self.store,run['runId'],'catalog',state='completed')
        self.assertEqual(_scheduled_sources(self.root,self.store,'it',NOW+86400,automation,policy)[0],[])
        self.assertEqual(_scheduled_sources(self.root,self.store,'it',NOW+2*86400,automation,policy)[0],['campaign'])

    def test_failed_downstream_stage_retries_original_run_after_cooldown(self):
        save_setting(self.store,'it','retry-setting-001',0,
                     {'automaticOperationsEnabled':True,'fullCatalogWeeklyEnabled':False})
        class FailingExecutor(FakeExecutor):
            def execute(self,store,run,stage,jobs):
                result=super().execute(store,run,stage,jobs)
                if stage=='taplink_prepare':result.update(state='failed',complete=False,
                                                          errorCode='temporary_failure',writeEvidence='zero')
                return result
        executor=FailingExecutor()
        for offset in range(4):
            tick(self.root,now=NOW+offset,executor=executor)
            if workflow_status(self.store)['current']['state']=='failed':break
        failed=workflow_status(self.store)['current'];prior_calls=len(executor.calls)
        tick(self.root,now=failed['finishedAt']+3599,executor=executor)
        self.assertEqual(len(executor.calls),prior_calls)
        tick(self.root,now=failed['finishedAt']+3601,executor=executor)
        self.assertEqual([stage for _,stage in executor.calls[prior_calls:]],['taplink_prepare'])
        self.assertEqual(workflow_status(self.store)['current']['runId'],failed['runId'])

    def test_unknown_failure_is_not_retried_as_a_new_platform_intent(self):
        save_setting(self.store,'it','unknown-setting-001',0,
                     {'automaticOperationsEnabled':True,'fullCatalogWeeklyEnabled':False})
        class UnknownExecutor(FakeExecutor):
            def execute(self,store,run,stage,jobs):
                result=super().execute(store,run,stage,jobs)
                if stage=='catalog':result.update(state='failed',complete=False,
                                                  errorCode='selection_result_unknown')
                return result
        executor=UnknownExecutor()
        tick(self.root,now=NOW,executor=executor)
        tick(self.root,now=NOW+1,executor=executor)
        failed=workflow_status(self.store)['current'];prior_calls=len(executor.calls)
        tick(self.root,now=failed['finishedAt']+3601,executor=executor)
        self.assertEqual(len(executor.calls),prior_calls)
        self.assertEqual(workflow_status(self.store)['current']['state'],'needs_human')

    def _write_stage_fixture(self,runner,request_id):
        from lib.operations_workflow import create_run
        save_setting(self.store,'it',request_id+'-setting',0,
                     {'automaticOperationsEnabled':True,'fullCatalogWeeklyEnabled':False})
        run=create_run(self.store,market='it',trigger_source='schedule',scheduled_at=NOW,
                       request_id=request_id,only_stage='taplink_prepare',sources=['campaign'])
        class LinkExecutor(SubprocessStageExecutor):
            def execute(self,store,run,stage,jobs):
                self.stage_calls=getattr(self,'stage_calls',0)+1
                first=self._call(['scripts/catalog-link-batch.py','--route','campaign','--creates','1'],'link-create-a')
                if first['state']!='completed':return first
                return self._call(['scripts/catalog-link-batch.py','--route','campaign','--creates','1'],'link-create-b')
        return run,LinkExecutor(self.root,runner=runner)

    def _stage_row(self,run_id):
        row=self.store.db.execute("SELECT platform_writes,counts_json,state FROM workflow_stage_run WHERE run_id=? "
                                  "AND stage='taplink_prepare'",(run_id,)).fetchone()
        return row['state'],row['platform_writes'],json.loads(row['counts_json'])

    def test_lost_stdout_keeps_report_writes_and_is_never_retried_as_zero(self):
        class Result:
            returncode=0;stdout='';stderr=''
        def runner(command,**_kwargs):
            Path(command[command.index('--report')+1]).write_text(json.dumps({'platformWrites':1}))
            return Result()
        run,executor=self._write_stage_fixture(runner,'lost-stdout-0001')
        tick(self.root,now=NOW,executor=executor)
        state,writes,counts=self._stage_row(run['runId'])
        self.assertEqual((state,writes,counts['writeEvidence']),('failed',1,'uncertain'))
        tick(self.root,now=workflow_status(self.store)['current']['finishedAt']+3601,executor=executor)
        self.assertEqual(executor.stage_calls,1)
        self.assertEqual(workflow_status(self.store)['current']['state'],'needs_human')

    def test_timeout_after_an_earlier_write_keeps_that_write_and_stays_unknown(self):
        class Result:
            returncode=0;stderr='';stdout='{"state":"completed","platformWrites":2}\n'
        def runner(command,**_kwargs):
            if 'link-create-b' in command[command.index('--report')+1]:raise subprocess.TimeoutExpired(command,1)
            return Result()
        run,executor=self._write_stage_fixture(runner,'timeout-after-write-0001')
        tick(self.root,now=NOW,executor=executor)
        state,writes,counts=self._stage_row(run['runId'])
        self.assertEqual((state,writes,counts['writeEvidence']),('failed',2,'uncertain'))
        tick(self.root,now=workflow_status(self.store)['current']['finishedAt']+3601,executor=executor)
        self.assertEqual(executor.stage_calls,1)

    def test_child_reported_zero_write_failure_keeps_bounded_auto_retry(self):
        class Result:
            returncode=1;stderr='';stdout='{"state":"failed","error":"temporary_read_failure","platformWrites":0}\n'
        run,executor=self._write_stage_fixture(lambda *_args,**_kwargs:Result(),'proven-zero-0001')
        tick(self.root,now=NOW,executor=executor)
        state,writes,counts=self._stage_row(run['runId'])
        self.assertEqual((state,writes,counts['writeEvidence']),('failed',0,'zero'))
        tick(self.root,now=workflow_status(self.store)['current']['finishedAt']+3601,executor=executor)
        self.assertEqual(executor.stage_calls,2)
        self.assertEqual(workflow_status(self.store)['current']['runId'],run['runId'])

    def test_two_ready_kalodata_markets_execute_together_but_no_more_than_policy_cap(self):
        from lib.operations_workflow import create_run,start_stage,finish_stage
        for market in ('br','uk'):
            self.store.plan('bjn-local-research',market)
            run=create_run(self.store,market=market,trigger_source='manual',scheduled_at=NOW+86400,
                           request_id=f'parallel-{market}-request',sources=['campaign'])
            for stage in ('catalog','taplink_prepare'):
                start_stage(self.store,run['runId'],stage)
                finish_stage(self.store,run['runId'],stage,state='completed')
        barrier=threading.Barrier(2);calls=[];guard=threading.Lock()
        class ConcurrentExecutor:
            def execute(self,_store,run,stage,_jobs):
                with guard:calls.append((run['market'],stage))
                barrier.wait(timeout=2)
                return {'state':'completed','itemCount':1,'complete':True,'platformWrites':0,
                        'scope':{'market':run['market']},'payload':{}}
        progress=tick(self.root,now=NOW+86400,executor=ConcurrentExecutor())
        self.assertEqual(progress['parallelMarkets'],['br','uk'])
        self.assertEqual(sorted(calls),[('br','kalodata'),('uk','kalodata')])

    def test_catalog_on_different_supply_accounts_runs_one_market_per_tick(self):
        from lib.operations_workflow import create_run
        for market in ('br','uk'):
            self.store.plan('bjn-local-research',market)
            create_run(self.store,market=market,trigger_source='manual',scheduled_at=NOW,
                       request_id=f'supply-parallel-{market}',only_stage='catalog',sources=['campaign'])
        calls=[]
        class RecordingCatalog:
            def execute(self,_store,run,stage,_jobs):
                calls.append((run['market'],stage))
                return {'state':'completed','itemCount':1,'complete':True,'platformWrites':0}
        progress=tick(self.root,now=NOW,executor=RecordingCatalog())
        self.assertEqual(progress['parallelMarkets'],['br'])
        self.assertEqual(calls,[('br','catalog')])
        progress=tick(self.root,now=NOW+1,executor=RecordingCatalog())
        self.assertEqual(progress['parallelMarkets'],['uk'])
        self.assertEqual(calls,[('br','catalog'),('uk','catalog')])

    def test_manual_market_takes_the_platform_slot_and_the_scheduled_market_follows(self):
        from lib.operations_workflow import create_run
        self.store.plan('bjn-local-research','br')
        save_setting(self.store,'it','scheduler-priority-setting',0,{'automaticOperationsEnabled':True})
        scheduled=create_run(self.store,market='it',trigger_source='schedule',scheduled_at=NOW-100,
                             sources=['campaign'])
        manual=create_run(self.store,market='br',trigger_source='manual',scheduled_at=NOW,
                          request_id='manual-priority-br',only_stage='oecid',sources=['campaign'])
        executor=FakeExecutor();progress=tick(self.root,now=NOW,executor=executor)
        self.assertEqual(progress['runId'],manual['runId'])
        self.assertEqual(progress['parallelMarkets'],['br'])
        self.assertEqual(executor.calls,[(manual['runId'],'oecid')])
        tick(self.root,now=NOW+1,executor=executor)
        self.assertEqual(executor.calls[-1],(scheduled['runId'],'taplink_clean'))

    def test_quota_and_unknown_market_do_not_block_another_kalodata_market(self):
        from lib.operations_workflow import create_run
        for market in ('br','uk'):self.store.plan('bjn-local-research',market)
        runs={market:create_run(self.store,market=market,trigger_source='manual',scheduled_at=NOW,
                                request_id=f'parallel-fault-{market}',only_stage='kalodata',sources=['campaign'])
              for market in ('it','br','uk')}
        calls=[]
        class FaultExecutor:
            def execute(self,_store,run,_stage,_jobs):
                calls.append(run['market']);state={'br':'quota_exhausted','it':'needs_human','uk':'completed'}[run['market']]
                return {'state':state,'itemCount':1,'complete':state=='completed','platformWrites':0,
                        'errorCode':'result_unknown' if state=='needs_human' else None}
        first=tick(self.root,now=NOW,executor=FaultExecutor())
        self.assertEqual(first['parallelMarkets'],['br','it'])
        self.assertEqual(len(calls),2)
        second=tick(self.root,now=NOW+10,executor=FaultExecutor())
        self.assertEqual(second['parallelMarkets'],['uk'])
        self.assertEqual(sorted(calls),['br','it','uk'])
        self.assertEqual(workflow_status(self.store,'it')['current']['state'],'needs_human')
        self.assertEqual(workflow_status(self.store,'uk')['current']['state'],'completed')

    def test_dead_claim_recovers_original_checkpoint_before_reexecution(self):
        from lib.operations_workflow import create_run,update_checkpoint
        from lib.workflow_resources import claim
        run=create_run(self.store,market='it',trigger_source='manual',scheduled_at=NOW,
                       request_id='recover-stage-request',only_stage='catalog',sources=['campaign'])
        stage=next(item for item in run['stages'] if item['state']=='queued')
        claim(self.store,stage['stageRunId'],'scheduler-dead-owner',[('workflow:it',1),('supply:acc9',1)],
              lease_seconds=5,worker_pid=999999)
        update_checkpoint(self.store,run['runId'],'catalog','page',{'number':7})
        seen=[]
        class RecoveryExecutor:
            def execute(self,_store,current,stage,_jobs):
                seen.append((stage,next(row['checkpoint'] for row in current['stages'] if row['stage']==stage)))
                return {'state':'completed','itemCount':1,'complete':True,'platformWrites':0}
        first=tick(self.root,now=NOW+100,executor=RecoveryExecutor())
        self.assertEqual(first['recoveredStageRuns'],[stage['stageRunId']])
        tick(self.root,now=NOW+110,executor=RecoveryExecutor())
        # A write-capable stage whose owner died may have written: it waits for review, never re-runs.
        self.assertEqual(seen,[])
        row=self.store.db.execute('SELECT state,error_code FROM workflow_stage_run WHERE stage_run_id=?',(stage['stageRunId'],)).fetchone()
        self.assertEqual(tuple(row),('needs_human','recovery_claim_expired_write_unknown'))

    def test_a_result_arriving_after_its_lease_lapsed_still_settles(self):
        from lib.operations_workflow import create_run,stage_state
        run=create_run(self.store,market='it',trigger_source='manual',scheduled_at=NOW,
                       request_id='late-settle-request',only_stage='oecid',sources=['campaign'])
        clock=[NOW+100]
        class SlowExecutor:
            def execute(self,_store,_current,stage,_jobs):
                clock[0]+=1000  # well past the 300 s lease, with no heartbeat in between
                return {'state':'completed','itemCount':2,'complete':True,'platformWrites':0}
        tick(self.root,clock=lambda:clock[0],executor=SlowExecutor())
        self.assertEqual(stage_state(self.store,run['runId'],'oecid')['state'],'completed')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM workflow_stage_claim').fetchone()[0],0)

    def test_dead_read_only_claim_resumes_from_its_checkpoint(self):
        from lib.operations_workflow import create_run,update_checkpoint
        from lib.workflow_resources import claim
        run=create_run(self.store,market='it',trigger_source='manual',scheduled_at=NOW,
                       request_id='recover-oecid-request',only_stage='oecid',sources=['campaign'])
        stage=next(item for item in run['stages'] if item['state']=='queued')
        self.assertEqual(stage['stage'],'oecid')
        claim(self.store,stage['stageRunId'],'scheduler-dead-owner',[('workflow:it',1),('communications:acc6',1)],
              lease_seconds=5,worker_pid=999999)
        update_checkpoint(self.store,run['runId'],'oecid','page',{'number':7})
        seen=[]
        class RecoveryExecutor:
            def execute(self,_store,current,stage,_jobs):
                seen.append((stage,next(row['checkpoint'] for row in current['stages'] if row['stage']==stage)))
                return {'state':'completed','itemCount':1,'complete':True,'platformWrites':0}
        tick(self.root,now=NOW+100,executor=RecoveryExecutor())
        tick(self.root,now=NOW+110,executor=RecoveryExecutor())
        self.assertEqual(seen,[('oecid',{'key':'page','value':{'number':7}})])


class StageWiring(unittest.TestCase):
    def setUp(self):
        # Legacy stage contracts must not depend on the deployment's selected role.
        accounts=json.loads((ROOT/'config/market-accounts.json').read_text())
        for pair in accounts['markets'].values():pair.update(imSessionMode='http_polling',identityAccountRole='communications')
        self.account_fixture=accounts
        p=patch('lib.market_accounts.load_config',return_value=accounts);p.start();self.addCleanup(p.stop)

    def test_supply_identity_uses_bounded_route_after_it_legacy_reconcile(self):
        self.account_fixture['markets']['it']['identityAccountRole']='supply'
        def answers(args,label):
            return {'state':'completed','itemCount':3,'complete':True,'platformWrites':0,'payload':{'sliceComplete':True,'queue':{}}}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'market':'it','runId':'r','applicableSources':['campaign']},'oecid',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        self.assertEqual([a[0] for a,_ in calls],['scripts/second-cycle-identities.py','scripts/market-identity.py'])

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
            creating=args[0]=='scripts/catalog-link-batch.py' and args[args.index('--creates')+1]=='200'
            return {'state':'completed','itemCount':499 if args[0]=='scripts/catalog-link-batch.py' else 0,
                    'complete':True,'platformWrites':267 if creating else 0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'applicableSources':['campaign']},'taplink_prepare',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['itemCount'],499)
        self.assertEqual(result['platformWrites'],267)
        self.assertEqual([args[0] for args,_ in calls],
                         ['scripts/catalog-link-batch.py','scripts/catalog-names.py','scripts/catalog-link-batch.py'])
        self.assertEqual(calls[1][0][3:5],['--market','it'])
        self.assertEqual(calls[0][0][calls[0][0].index('--creates')+1],'0')
        self.assertEqual(calls[2][0][calls[2][0].index('--creates')+1],'200')

    def test_accepted_partial_snapshot_is_reused_until_its_selection_queue_is_drained(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir()
            with closing(sqlite3.connect(root/'var/global-source-uk.sqlite')) as db,db:
                db.executescript('CREATE TABLE global_source_head(scope_hash TEXT,run_id TEXT);'
                  'CREATE TABLE global_source_run(id TEXT,scope TEXT,state TEXT,terminal_reason TEXT,identity_unchanged INTEGER);'
                  'CREATE TABLE global_source_product(run_id TEXT,pid TEXT);')
                db.execute("INSERT INTO global_source_head VALUES('h','source')")
                db.execute("INSERT INTO global_source_run VALUES('source',?,'accepted_partial','operator_accepted_partial',1)",(json.dumps({'market':'uk'}),))
                db.execute("INSERT INTO global_source_product VALUES('source','1')")
            with closing(sqlite3.connect(root/'var/global-selection-uk.sqlite')) as db,db:
                db.executescript('CREATE TABLE intake_run(id TEXT,source_run TEXT,created REAL);CREATE TABLE intake_item(run_id TEXT,state TEXT);')
                db.execute("INSERT INTO intake_run VALUES('intake','source',1)");db.execute("INSERT INTO intake_item VALUES('intake','pending')")
            self.assertEqual(accepted_partial_selection(root,'uk')['states'],{'pending':1})
            with closing(sqlite3.connect(root/'var/global-selection-uk.sqlite')) as db,db:db.execute("UPDATE intake_item SET state='confirmed'")
            self.assertIsNone(accepted_partial_selection(root,'uk'))

    def test_explicit_login_rejection_is_detected_without_treating_other_unknowns_as_auth(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir()
            with closing(sqlite3.connect(root/'var/global-selection-uk.sqlite')) as db,db:
                db.execute('CREATE TABLE intake_item(state TEXT,payload TEXT)')
                db.execute("INSERT INTO intake_item VALUES('result_unknown',?)",(json.dumps({'attemptedAt':10,'receipt':{'http':200,'code':16201010,'ambiguous':False}}),))
                db.execute("INSERT INTO intake_item VALUES('result_unknown',?)",(json.dumps({'attemptedAt':11,'receipt':{'http':0,'code':None,'ambiguous':True}}),))
            self.assertEqual(selection_auth_unknown(root,'uk'),{'count':1,'latestAttempt':10})

    def test_br_taplink_and_kalodata_are_pinned_to_br(self):
        def answers(args,_label):
            payload={'missing':0} if args[0]=='scripts/catalog-names.py' else \
                {'market':'br','sliceComplete':True,'completed':1,'A':1,'B':0,'fragments':1,'networkRequests':1}
            return {'state':'completed','itemCount':1,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        self.assertEqual(executor.execute(None,{'market':'br','applicableSources':['campaign']},'taplink_prepare',{'jobs':{}})['state'],'completed')
        self.assertTrue(all('--market' in args and args[args.index('--market')+1]=='br' for args,_ in calls))
        calls.clear();result=executor.execute(None,{'market':'br','applicableSources':['campaign']},'kalodata',{'jobs':{}})
        self.assertEqual((result['state'],result['itemCount']),('completed',1))
        self.assertEqual(calls[0][0][:4],['scripts/rolling-leads.py','run','--market','br'])

    def test_it_kalodata_and_send_pool_pass_required_market(self):
        def answers(args,_label):
            if args[0]=='scripts/rolling-leads.py':
                return {'state':'failed','itemCount':0,'complete':False,'platformWrites':0,
                        'errorCode':'preflight-test','payload':{}}
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,
                    'payload':{'counts':{'positions':1}}}
        executor,calls=self.executor(answers)
        executor.execute(None,{'market':'it','applicableSources':['campaign']},'kalodata',{'jobs':{}})
        self.assertEqual(calls[0][0][:4],['scripts/rolling-leads.py','run','--market','it'])
        calls.clear()
        result=executor.execute(None,{'market':'it','applicableSources':['campaign']},'send_pool',{'jobs':{}})
        self.assertEqual(result['itemCount'],1)
        self.assertEqual(calls[0][0][-2:],['--market','it'])

    def test_non_it_selected_taplink_seeds_the_full_live_pool(self):
        def answers(args,_label):
            payload={'missing':0} if args[0]=='scripts/catalog-names.py' else {}
            return {'state':'completed','itemCount':7940,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'market':'uk','applicableSources':['selected']},'taplink_prepare',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        seed=calls[0][0]
        self.assertEqual(seed[seed.index('--scope')+1],'pool')
        self.assertEqual(seed[seed.index('--market')+1],'uk')

    def test_non_it_oecid_runs_until_the_pending_queue_is_empty(self):
        runs=iter(({'resolvedHandles':1,'unresolvedHandles':0,'newBindings':2},
                   {'resolvedHandles':0,'unresolvedHandles':1,'newBindings':0}))
        statuses=iter(({'items':[{'handle':'next'}]},{'items':[]}))
        def answers(args,_label):
            payload=next(runs) if args[1]=='run' else next(statuses)
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'market':'uk','applicableSources':['selected']},'oecid',{'jobs':{}})
        self.assertEqual((result['state'],result['itemCount'],result['scope']['pending']),('completed',2,0))
        self.assertEqual([args[1] for args,_ in calls],['run','status','run','status'])

    def test_non_it_oecid_relogs_the_communications_account_for_explicit_auth_failure(self):
        runs=iter(({'error':'market_identity_auth_required'},{'resolvedHandles':1,'unresolvedHandles':0,'newBindings':1}))
        def answers(args,_label):
            if args[1]=='run':
                payload=next(runs);state='needs_human' if payload.get('error') else 'completed'
                return {'state':state,'itemCount':0,'complete':state=='completed','platformWrites':0,
                        'errorCode':payload.get('error'),'payload':payload}
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':{'items':[]}}
        executor,calls=self.executor(answers);relogins=[]
        executor._relogin_market_account=lambda store,market,role,run_id,reason:(relogins.append((market,role)) or {'state':'completed','generationId':'g2'})
        result=executor.execute(object(),{'runId':'workflow-br','market':'br','applicableSources':['campaign']},'oecid',{'jobs':{}})
        self.assertEqual(result['state'],'completed');self.assertEqual(result['scope']['authRecoveries'],1)
        self.assertEqual(relogins,[('br','communications')])
        self.assertEqual([args[1] for args,_ in calls],['run','run','status'])

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
        self.assertEqual([args[:2] for args,_ in calls],[['scripts/campaign-join.py','join-all'],['scripts/campaign-collect.py','--max-requests'],
                         ['scripts/campaign-join.py','status']])
        self.assertTrue(all(args[2:4]==['--market','it'] for args,_ in calls
                            if args[0]=='scripts/campaign-join.py'))

    def test_it_campaign_join_verification_keeps_explicit_market_scope(self):
        def answers(args,_label):
            if args[1]=='status':payload={'available':True,'unresolved':['123']}
            elif args[1]=='verify':payload={'unresolved':[]}
            elif args[1]=='join-all':payload={'state':'completed','unresolved':[]}
            else:payload={'status':'completed','screening':{'recorded':True},'offers':7}
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'market':'it','applicableSources':['campaign']},'catalog',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        self.assertEqual([args[1] for args,_ in calls if args[0]=='scripts/campaign-join.py'],
                         ['join-all','status'])
        self.assertTrue(all(args[2:4]==['--market','it'] for args,_ in calls
                            if args[0]=='scripts/campaign-join.py'))

    def test_unknown_campaign_prepares_known_goods_before_bounded_verification(self):
        def answers(args,label):
            payload={};writes=0
            if args[1]=='join-all':payload={'state':'needs_verification','unresolved':['123']};writes=10
            elif args[1]=='status':payload={'activeVerification':['123'],'unresolved':['123']}
            elif args[1]=='verify':payload={'state':'needs_verification','unresolved':['123'],'stoppedUnknown':['123']}
            elif args[0]=='scripts/campaign-collect.py':payload={'status':'completed','screening':{'recorded':True},'offers':9}
            elif label=='taplink-create-campaign':writes=9
            return {'state':'completed','itemCount':9,'complete':True,'platformWrites':writes,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'market':'it','applicableSources':['campaign']},'catalog',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['itemCount'],9)
        self.assertEqual(result['platformWrites'],19)
        labels=[label for _,label in calls]
        self.assertLess(labels.index('campaign-catalog'),labels.index('taplink-create-campaign'))
        self.assertLess(labels.index('taplink-create-campaign'),labels.index('campaign-bounded-verification'))
        verify_args=next(args for args,label in calls if label=='campaign-bounded-verification')
        self.assertIn('--bounded',verify_args)
        self.assertEqual(verify_args[2:4],['--market','it'])
        self.assertEqual(result['scope']['campaignApplications']['stoppedUnknown'],['123'])

    def test_join_failure_does_not_block_existing_catalog_but_remains_explicit_gap(self):
        def answers(args,label):
            if args[1]=='join-all':return {'state':'failed','platformWrites':0,'errorCode':'campaign_list_incomplete','payload':{}}
            payload={'available':False} if args[1]=='status' else {'status':'completed','screening':{'recorded':True},'offers':7}
            return {'state':'completed','itemCount':7,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'market':'br','applicableSources':['campaign']},'catalog',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['itemCount'],7)
        self.assertEqual(result['scope']['campaignApplications']['errorCode'],'campaign_list_incomplete')
        self.assertEqual(result['scope']['campaignApplications']['state'],'failed')
        self.assertTrue(any(args[0]=='scripts/campaign-collect.py' for args,_ in calls))

    def test_late_confirmed_campaign_republishes_catalog_once_without_rejoining(self):
        def answers(args,label):
            payload={}
            if args[1]=='join-all':payload={'state':'needs_verification','unresolved':['123']}
            elif args[1]=='status':payload={'activeVerification':['123']}
            elif args[1]=='verify':payload={'unresolved':[],'joinedSettled':1}
            elif args[0]=='scripts/campaign-collect.py':payload={'status':'completed','screening':{'recorded':True},'offers':10 if label=='campaign-confirmed-catalog' else 9}
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'market':'br','applicableSources':['campaign']},'catalog',{'jobs':{}})
        self.assertEqual(result['itemCount'],10)
        self.assertEqual(sum(args[1]=='join-all' for args,_ in calls),1)
        self.assertEqual(sum(args[0]=='scripts/campaign-collect.py' for args,_ in calls),2)
        self.assertEqual(result['scope']['campaignApplications']['unresolved'],[])

    def test_campaign_only_market_never_calls_full_managed_worker(self):
        def answers(args,_label):
            payload={'available':False} if args[1]=='status' else {'state':'completed'} if args[1]=='join-all' else {'status':'completed','screening':{'recorded':True},'offers':3}
            return {'state':'completed','itemCount':3,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        for market in ('br','my'):
            calls.clear();result=executor.execute(None,{'market':market,'applicableSources':['campaign']},'catalog',{'jobs':{}})
            self.assertEqual(result['state'],'completed')
            self.assertEqual([args[0] for args,_ in calls],['scripts/campaign-join.py','scripts/campaign-collect.py','scripts/campaign-join.py'])
            self.assertTrue(all('--market' in args and args[args.index('--market')+1]==market for args,_ in calls))

    def test_new_market_full_managed_catalog_uses_first_level_category_partitions(self):
        def answers(args,_label):
            if args[0]=='scripts/collect-global-opportunity.py':payload={'state':'completed','published':True,'products':31809};writes=0
            elif args[0]=='scripts/sync-cycle-catalog.py':payload={'status':'completed','offers':2321};writes=0
            elif args[1] in ('prepare','verify'):payload={'states':{'pending':21},'error':None};writes=0
            else:payload={'states':{'confirmed':21},'error':None};writes=21
            return {'state':'completed','itemCount':31809,'complete':True,'platformWrites':writes,'payload':payload}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
            (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
            executor=SubprocessStageExecutor(root);calls=[]
            executor._call=lambda args,label,timeout=14400:(calls.append((args,label)) or answers(args,label))
            result=executor.execute(None,{'runId':'workflow-test','market':'uk','applicableSources':['selected']},'catalog',{'jobs':{}})
            self.assertEqual((result['state'],result['itemCount']),('completed',31809))
            self.assertIn('--by-category',calls[0][0])
            self.assertEqual(result['platformWrites'],21)
            self.assertEqual([call[0][0] for call in calls],
                             ['scripts/collect-global-opportunity.py','scripts/select-global-products.py','scripts/select-global-products.py',
                              'scripts/select-global-products.py','scripts/sync-cycle-catalog.py'])

    def _selection_catalog(self,ledger_rows,failures=None):
        calls=[]
        failures=failures if failures is not None else [{'report':None,'states':{'pending':2,'result_unknown':1}}]
        def answers(args,_label):
            if args[0]=='scripts/collect-global-opportunity.py':payload={'state':'completed','published':True,'products':3}
            elif args[0]=='scripts/sync-cycle-catalog.py':payload={'status':'completed','offers':2}
            elif args[0]=='scripts/campaign-join.py':payload={'state':'completed','unresolved':[],'stoppedUnknown':[]}
            elif args[0]=='scripts/campaign-collect.py':payload={'status':'completed','offers':5,'screening':{'recorded':True}}
            elif args[1] in ('prepare','verify'):payload={'states':{'pending':3},'error':None}
            elif sum(1 for a,_ in calls if a[:2]==['scripts/select-global-products.py','execute-fast'])<=len(failures):
                payload=failures[sum(1 for a,_ in calls if a[:2]==['scripts/select-global-products.py','execute-fast'])-1]
                return {'state':'failed','itemCount':0,'complete':False,'platformWrites':2,
                        'errorCode':'parallel_selection_requires_review','payload':payload}
            else:payload={'states':{'confirmed':2,'result_unknown':len(ledger_rows),'pending':0},'error':None}
            return {'state':'completed','itemCount':3,'complete':True,'platformWrites':0,'payload':payload}
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        root=Path(folder.name);(root/'var').mkdir();(root/'config').mkdir()
        (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
        with closing(sqlite3.connect(root/'var/global-selection-uk.sqlite')) as db,db:
            db.execute('CREATE TABLE intake_item(run_id TEXT,pid TEXT,state TEXT,payload TEXT,updated REAL)')
            db.executemany("INSERT INTO intake_item VALUES('run',?,'result_unknown',?,0)",ledger_rows)
        executor=SubprocessStageExecutor(root)
        executor._call=lambda args,label,timeout=14400:(calls.append((args,label)) or answers(args,label))
        result=executor.execute(None,{'runId':'workflow-gap','market':'uk','applicableSources':['selected','campaign']},'catalog',{'jobs':{}})
        return result,calls

    def test_single_pid_selection_unknown_is_isolated_and_campaign_continues(self):
        receipt=json.dumps({'receipt':{'http':200,'code':0,'ambiguous':True}})
        result,calls=self._selection_catalog([('pid-unknown',receipt)])
        self.assertEqual((result['state'],result['complete']),('completed',True))
        self.assertEqual(result['scope']['selectionGap'],{'unresolvedPids':['pid-unknown'],'coverage':'partial_selection',
            'reasons':['global_selection_unresolved','parallel_selection_requires_review']})
        self.assertEqual(result['platformWrites'],2)
        self.assertTrue(any(args[0]=='scripts/campaign-collect.py' for args,_ in calls))
        self.assertTrue(any(args[0]=='scripts/sync-cycle-catalog.py' for args,_ in calls))

    def test_tolerated_selection_failures_stop_on_their_own_progress(self):
        receipt=json.dumps({'receipt':{'http':200,'code':0,'ambiguous':True}})
        executes=lambda calls:sum(1 for a,_ in calls if a[:2]==['scripts/select-global-products.py','execute-fast'])
        # Nothing left to select: a stable tolerated failure ends with the gap instead of 40 retries.
        result,calls=self._selection_catalog([('pid-unknown',receipt)],[{'states':{'pending':0,'result_unknown':1}}]*40)
        self.assertEqual((result['state'],executes(calls)),('completed',1))
        self.assertEqual(result['scope']['selectionGap']['unresolvedPids'],['pid-unknown'])
        # The same pending count twice is no progress; an unreadable count is unknown, never zero.
        result,calls=self._selection_catalog([('pid-unknown',receipt)],[{'states':{'pending':5}}]*40)
        self.assertEqual((result['state'],result['errorCode'],executes(calls)),('needs_human','global_selection_no_progress',2))
        result,calls=self._selection_catalog([('pid-unknown',receipt)],[{'report':None}]*40)
        self.assertEqual((result['state'],result['errorCode'],executes(calls)),('needs_human','global_selection_progress_unknown',1))

    def test_account_login_failure_or_many_unknowns_still_stop_dependent_writes(self):
        auth=json.dumps({'receipt':{'http':200,'code':16201010,'ambiguous':False}})
        ambiguous=json.dumps({'receipt':{'http':200,'code':0,'ambiguous':True}})
        for rows in ([('pid-auth',auth)],[(f'pid-{n}',ambiguous) for n in range(4)]):
            result,calls=self._selection_catalog(rows)
            self.assertEqual((result['state'],result['errorCode']),('failed','parallel_selection_requires_review'))
            self.assertFalse(any(args[0] in ('scripts/campaign-collect.py','scripts/campaign-join.py') for args,_ in calls))

    def test_uk_weekly_maintenance_reuses_discovery_within_fifteen_days(self):
        def answers(args,_label):
            if args[0]=='scripts/collect-global-opportunity.py':payload={'state':'completed','published':True,'products':10000};writes=0
            elif args[0]=='scripts/sync-cycle-catalog.py':payload={'status':'completed','offers':12};writes=0
            elif args[1] in ('prepare','verify'):payload={'states':{'pending':0},'error':None};writes=0
            else:payload={'states':{'confirmed':0},'error':None};writes=0
            return {'state':'completed','itemCount':10000,'complete':True,'platformWrites':writes,'payload':payload}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
            (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
            with closing(sqlite3.connect(root/'var/global-source-uk.sqlite')) as db,db:
                db.executescript('CREATE TABLE global_source_run(id TEXT,scope TEXT,state TEXT,updated REAL,identity_unchanged INTEGER);'
                  'CREATE TABLE global_source_operator_acceptance(run_id TEXT,accepted_at REAL);'
                  'CREATE TABLE global_source_head(run_id TEXT);CREATE TABLE global_source_product(run_id TEXT,pid TEXT);')
                db.execute("INSERT INTO global_source_run VALUES('r',?,'accepted_partial',?,1)",(json.dumps({'market':'uk','partitionMode':'category_l1_v1'}),NOW-86400))
                db.execute("INSERT INTO global_source_operator_acceptance VALUES('r',?)",(NOW-86400,))
                db.execute("INSERT INTO global_source_head VALUES('r')")
            executor=SubprocessStageExecutor(root);calls=[]
            executor._call=lambda args,label,timeout=14400:(calls.append((args,label)) or answers(args,label))
            result=executor.execute(None,{'runId':'workflow-uk','market':'uk','applicableSources':['selected']},'catalog',{'jobs':{}})
            self.assertEqual(result['state'],'completed')
            self.assertFalse(any(args[0]=='scripts/collect-global-opportunity.py' for args,_ in calls))
            self.assertTrue(any(args[0]=='scripts/sync-cycle-catalog.py' for args,_ in calls))

    def test_weekly_overlay_published_from_accepted_category_is_a_valid_catalog_generation(self):
        def answers(args,_label):
            if args[0]=='scripts/collect-global-opportunity.py':
                payload={'state':'accepted_partial','published':True,'products':3,
                         'coverageOverlay':{'baselineRunId':'base','refreshRunId':'weekly'}}
            elif args[0]=='scripts/sync-cycle-catalog.py':payload={'status':'completed'}
            elif args[1] in ('prepare','verify'):payload={'states':{'pending':0},'error':None}
            elif args[1]=='execute-fast':payload={'states':{'pending':0},'error':None}
            else:payload={}
            return {'state':'completed','itemCount':3,'complete':True,'platformWrites':0,'payload':payload}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
            (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
            executor=SubprocessStageExecutor(root);calls=[]
            executor._call=lambda args,label,timeout=14400:(calls.append(args) or answers(args,label))
            result=executor.execute(None,{'runId':'workflow-uk-overlay','market':'uk','applicableSources':['selected']},'catalog',{'jobs':{}})
            self.assertEqual(result['state'],'completed')
            self.assertTrue(any(args[0]=='scripts/collect-global-opportunity.py' for args in calls))

    def test_category_refresh_returns_after_thirty_days(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
            (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
            with closing(sqlite3.connect(root/'var/global-source-uk.sqlite')) as db,db:
                db.executescript('CREATE TABLE global_source_run(id TEXT,scope TEXT,state TEXT,updated REAL,identity_unchanged INTEGER);'
                  'CREATE TABLE global_source_operator_acceptance(run_id TEXT,accepted_at REAL);')
                db.execute("INSERT INTO global_source_run VALUES('r',?,'completed',?,1)",(json.dumps({'market':'uk','partitionMode':'category_l1_v1'}),NOW-31*86400))
                db.execute("INSERT INTO global_source_run VALUES('overlay',?,'completed',?,1)",(json.dumps({'market':'uk','partitionMode':'category_l1_v1','coverageOverlay':{'baselineRunId':'r'}}),NOW))
            self.assertEqual(full_catalog_collection_mode(root,'uk',NOW)['mode'],'category')

    def test_it_uses_category_discovery_and_no_history_means_onboarding(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
            (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
            with closing(sqlite3.connect(root/'var/global-source.sqlite')) as db,db:
                db.executescript('CREATE TABLE global_source_run(id TEXT,scope TEXT,state TEXT,updated REAL,identity_unchanged INTEGER);'
                  'CREATE TABLE global_source_operator_acceptance(run_id TEXT,accepted_at REAL);'
                  'CREATE TABLE global_source_head(scope_hash TEXT,run_id TEXT);')
                db.execute("INSERT INTO global_source_run VALUES('baseline',?,'completed',?,1)",
                           (json.dumps({'market':'it','partitionMode':'category_l1_v1'}),NOW-90*86400))
                db.execute("INSERT INTO global_source_head VALUES('scope','baseline')")
            self.assertEqual(full_catalog_collection_mode(root,'it',NOW)['mode'],'category')
            self.assertEqual(full_catalog_collection_mode(root,'it',NOW)['reason'],'periodic_category_discovery')
            with closing(sqlite3.connect(root/'var/global-source.sqlite')) as db,db:
                db.execute("DELETE FROM global_source_head")
            self.assertEqual(full_catalog_collection_mode(root,'it',NOW)['mode'],'category')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
            (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
            self.assertEqual(full_catalog_collection_mode(root,'it',NOW)['mode'],'category')

    def test_it_category_cli_accepts_read_only_status_without_starting_collection(self):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/collect-global-opportunity.py'),
                               '--market','it','--run-id','test-category-status','--status','--by-category'],
                              cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        self.assertNotIn('it_category_collection_disabled',result.stderr)

    def test_category_history_read_failure_never_defaults_to_an_expensive_category_crawl(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
            (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
            sqlite3.connect(root/'var/global-source-uk.sqlite').close()
            with self.assertRaisesRegex(ValueError,'category_history_unavailable'):
                full_catalog_collection_mode(root,'uk',NOW)

    def test_full_managed_catalog_never_resubmits_an_unknown_selection(self):
        def answers(args,_label):
            if args[0]=='scripts/collect-global-opportunity.py':payload={'state':'completed','published':True,'products':31809}
            elif args[1]=='prepare':payload={'states':{'result_unknown':1},'error':None}
            else:payload={'states':{'result_unknown':1},'error':None}
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        # This test targets the unknown selection barrier, not the independent live
        # IT category-baseline prerequisite stored only in production var/.
        with patch('lib.operations_policy.full_catalog_collection_mode',return_value={'mode':'plain'}):
            result=executor.execute(None,{'runId':'workflow-test','applicableSources':['selected']},
                                    'catalog',{'jobs':{}})
        self.assertEqual(result['state'],'needs_human')
        self.assertEqual(result['errorCode'],'global_selection_unresolved')
        self.assertEqual(len(calls),4)
        self.assertIn('--reconcile-rejections',calls[-1][0])

    def test_oecid_hands_off_every_current_batch_before_resolving(self):
        submissions=iter((['discovery_'+'1'*32],[]))
        def answers(args,_label):
            if args[0]=='scripts/second-cycle-identities.py':
                payload=({'submittedBatches':next(submissions)} if args[1]=='submit' else
                         {'newBindings':0,'batches':{}})
            else:
                payload={'pendingAtStart':3,'pending':0,'claimed':3,'stopReason':'backlog_clear'}
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        result=executor.execute(None,{'applicableSources':['campaign']},'oecid',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['itemCount'],3)
        self.assertEqual(result['scope']['handoffBatches'],1)
        self.assertEqual([args[0] for args,_ in calls],
                         ['scripts/second-cycle-identities.py','scripts/second-cycle-identities.py',
                          'scripts/second-cycle-identities.py',
                          'scripts/identity-batch.py'])

    def test_bounded_identity_slice_publishes_found_subset_and_preserves_pending_scope(self):
        def answers(args,_label):
            payload=({'submittedBatches':[]} if args[:2]==['scripts/second-cycle-identities.py','submit'] else
                     {'newBindings':0,'batches':{}} if args[0]=='scripts/second-cycle-identities.py' else
                     {'sliceComplete':True,'coverage':'bounded_identity_slice','pending':40,'claimed':50,'found':7,'notFound':3,'technicalIsolatedLeads':2,'stopReason':'retry_wait'})
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':payload}
        result=self.executor(answers)[0].execute(None,{'applicableSources':['campaign']},'oecid',{'jobs':{}})
        self.assertEqual(result['state'],'completed');self.assertEqual(result['itemCount'],7)
        self.assertEqual(result['scope']['pending'],40);self.assertEqual(result['scope']['technicalIsolatedLeads'],2)

    def test_oecid_never_publishes_with_a_stalled_pending_queue(self):
        def answers(args,_label):
            payload=({'submittedBatches':[]} if args[:2]==['scripts/second-cycle-identities.py','submit'] else
                     {'newBindings':0,'batches':{}} if args[0]=='scripts/second-cycle-identities.py' else
                     {'pendingAtStart':4,'pending':4,'claimed':0,'stopReason':'queue_stalled'})
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':payload}
        result=self.executor(answers)[0].execute(
            None,{'applicableSources':['campaign']},'oecid',{'jobs':{}})
        self.assertEqual(result['state'],'needs_human')
        self.assertFalse(result['complete'])
        self.assertEqual(result['errorCode'],'identity_queue_stalled')

    def test_kalodata_publishes_completed_subset_at_quota_with_remaining_scope(self):
        payload={'market':'it','sliceComplete':True,'completed':5,'A':3,'B':2,'fragments':8,'networkRequests':12,
                 'stopped':'kalodata_daily_quota_exhausted','queue':{'control':{'state':'waiting_quota'}}}
        executor,calls=self.executor(lambda *_:{'state':'completed','complete':True,'platformWrites':0,'payload':payload})
        result=executor.execute(None,{'applicableSources':['campaign']},'kalodata',{'jobs':{}})
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['itemCount'],5)
        self.assertEqual(result['scope']['aCompleted'],3);self.assertEqual(result['scope']['bCompleted'],2)
        self.assertEqual(result['scope']['remaining']['control']['state'],'waiting_quota')
        self.assertEqual(len(calls),1)

    def test_kalodata_has_a_finite_slice_not_an_all_due_products_barrier(self):
        payload={'market':'br','sliceComplete':True,'completed':2,'A':1,'B':1,'fragments':8,'networkRequests':24,
                 'queue':{'remaining':8000}}
        executor,calls=self.executor(lambda *_:{'state':'completed','complete':True,'platformWrites':0,'payload':payload})
        result=executor.execute(None,{'market':'br','applicableSources':['campaign']},'kalodata',{'jobs':{}})
        self.assertEqual((result['state'],result['itemCount'],len(calls)),('completed',2,1))
        self.assertEqual(result['scope']['coverage'],'bounded_query_slice')
        self.assertEqual(result['scope']['remaining']['remaining'],8000)

    def test_kalodata_single_pid_isolation_does_not_block_known_results(self):
        payload={'market':'br','sliceComplete':True,'completed':1,'A':1,'B':0,'fragments':2,'networkRequests':4,
                 'errors':[{'pid':'one','code':'technical_failure'}],'queue':{'isolated':1}}
        executor,_=self.executor(lambda *_:{'state':'completed','complete':True,'platformWrites':0,'payload':payload})
        result=executor.execute(None,{'market':'br','applicableSources':['campaign']},'kalodata',{'jobs':{}})
        self.assertEqual(result['state'],'completed');self.assertTrue(result['complete'])
        self.assertEqual(result['scope']['remaining']['isolated'],1)

    def test_identity_nothing_pending_is_success_for_the_exact_identity_cli(self):
        class Result:
            returncode=0;stderr='';stdout=json.dumps({'market':'uk','targets':0,'networkRuns':0,'stopped':'nothing_pending'})
        executor=SubprocessStageExecutor(ROOT,runner=lambda *_args,**_kwargs:Result())
        result=executor._call(['scripts/market-identity.py','run','--market','uk'],'identity-empty')
        self.assertEqual(result['state'],'completed')
        other=executor._call(['scripts/catalog-link-batch.py'],'not-identity')
        self.assertEqual(other['state'],'failed')

    def test_non_it_link_maintenance_is_a_read_only_binding_check(self):
        calls=[]
        executor=SubprocessStageExecutor(ROOT)
        executor._call=lambda args,label,timeout=14400:(calls.append(args) or {'state':'completed','complete':True,'platformWrites':0,
            'payload':{'summary':{'checked':4,'valid':3,'withdrawn':1,'unresolved':0}}})
        result=executor.execute(None,{'runId':'workflow-br','market':'br','applicableSources':['campaign']},'taplink_clean',{'jobs':{}})
        self.assertEqual(calls,[['scripts/catalog-clean.py','check-bindings','--market','br']])
        self.assertEqual((result['state'],result['itemCount'],result['platformWrites']),('completed',4,0))
        self.assertEqual(result['scope'],{'market':'br','mode':'binding_check_read_only','remoteDelete':'not_enabled'})

    def test_it_bounded_identity_slice_uses_the_same_four_market_contract(self):
        class Result:
            returncode=0;stderr=''
            def __init__(self,payload):self.stdout=json.dumps(payload)+'\n'
        base={'sliceComplete':True,'platformWrites':0,'realSends':0,'coverage':'bounded_identity_slice'}
        for market in ('it','br','my','uk'):
            for payload,count in ((base|{'market':market,'newBindings':3},3),
                                  (base|{'market':market,'newBindings':0,'stopped':'account_wait'},0),
                                  (base|{'market':market,'newBindings':0,'stopped':'nothing_pending'},0)):
                executor=SubprocessStageExecutor(ROOT,runner=lambda *_a,_p=payload,**_k:Result(_p))
                result=executor._call(['scripts/market-identity.py','run','--market',market],'identity-contract')
                self.assertEqual((market,result['state'],result['itemCount'],result['scope']),
                                 (market,'completed',count,{'coverage':'bounded_identity_slice'}))
        for payload in (base|{'market':'br','stopped':'account_wait'},
                        base|{'market':'it','platformWrites':1,'stopped':'account_wait'},
                        base|{'market':'it','realSends':1,'stopped':'account_wait'}):
            executor=SubprocessStageExecutor(ROOT,runner=lambda *_a,_p=payload,**_k:Result(_p))
            result=executor._call(['scripts/market-identity.py','run','--market','it'],'identity-contract')
            self.assertNotEqual(result.get('scope'),{'coverage':'bounded_identity_slice'})
            self.assertEqual(result['state'],'failed')

    def test_a_write_capable_child_must_state_its_writes_to_prove_zero(self):
        class Result:
            stderr=''
            def __init__(self,stdout,returncode=0):self.stdout=stdout;self.returncode=returncode
        cases=((['scripts/catalog-link-batch.py','--route','campaign','--creates','200'],'{"state":"completed"}','uncertain'),
               (['scripts/catalog-link-batch.py','--route','campaign','--creates','200'],'{"state":"completed","platformWrites":"3"}','uncertain'),
               (['scripts/catalog-link-batch.py','--route','campaign','--creates','200'],'{"state":"completed","platformWrites":0}',None),
               (['scripts/select-global-products.py','verify'],'{"states":{"pending":0}}',None),
               (['scripts/campaign-join.py','join-all','--market','it','--confirm'],'{"state":"completed"}','uncertain'))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir()
            for args,stdout,expected in cases:
                result=SubprocessStageExecutor(root,runner=lambda *_a,_o=stdout,**_k:Result(_o))._call(args,'evidence-fixture')
                self.assertEqual(result.get('writeEvidence'),expected,(args,stdout))

    def test_a_failed_inner_batch_step_is_never_a_completed_stage(self):
        class Result:
            returncode=0;stdout='{"steps": 2}\n';stderr=''
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir()
            def runner(command,**_kwargs):
                Path(command[command.index('--report')+1]).write_text(json.dumps({'steps':[
                    {'label':'read-00','exitCode':0,'result':{'state':'completed','platformWrites':0}},
                    {'label':'create-01','exitCode':0,'result':{'state':'blocked','platformWrites':1}}],
                    'finalSummary':{'total':3}}))
                return Result()
            result=SubprocessStageExecutor(root,runner=runner)._call(
                ['scripts/catalog-link-batch.py','--route','campaign','--creates','200'],'nested-failure-fixture')
        self.assertEqual((result['state'],result['errorCode'],result['platformWrites']),
                         ('failed','nested_create-01_blocked',1))

    def test_batch_outcome_never_turns_a_missing_create_count_into_zero(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('catalog_link_batch',ROOT/'scripts/catalog-link-batch.py')
        batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)
        ok={'label':'create-01','exitCode':0,'result':{'state':'completed','platformWrites':2}}
        read={'label':'read-00','exitCode':0,'result':{'state':'completed','platformWrites':0}}
        self.assertEqual(batch.batch_outcome([read]),{'state':'completed','platformWrites':0,'writeEvidence':'zero'})
        self.assertEqual(batch.batch_outcome([read,ok])['writeEvidence'],'known')
        for broken in ({'label':'create-02','exitCode':0,'result':None},
                       {'label':'create-02','exitCode':1,'result':{'state':'blocked','platformWrites':1}},
                       {'label':'create-02','exitCode':0,'result':{'state':'blocked','platformWrites':1,'writeEvidence':'uncertain'}}):
            outcome=batch.batch_outcome([read,ok,broken])
            self.assertEqual((outcome['state'],outcome['writeEvidence']),('blocked','uncertain'),broken)
            self.assertGreaterEqual(outcome['platformWrites'],2)

    def test_success_exit_without_valid_report_or_with_busy_lock_is_failure(self):
        class Result:
            returncode=0;stderr=''
            def __init__(self,stdout):self.stdout=stdout
        outputs=iter(('', '{"done":0,"targets":3,"dueQueue":3,"stopped":"browser_lock_busy"}\n'))
        executor=SubprocessStageExecutor(ROOT,runner=lambda *_args,**_kwargs:Result(next(outputs)))
        invalid=executor._call(['scripts/leads-run.py'],'missing-output')
        busy=executor._call(['scripts/leads-run.py'],'busy-lock')
        self.assertEqual(invalid['errorCode'],'missing-output_report_invalid')
        self.assertEqual((invalid['state'],busy['state'],busy['errorCode']),
                         ('failed','failed','browser_lock_busy'))

    def test_stage_adapter_preserves_an_explicit_needs_human_result(self):
        class Result:
            returncode=0;stdout='{"state":"needs_human","error":"verification_pending"}\n';stderr=''
        executor=SubprocessStageExecutor(ROOT,runner=lambda *_args,**_kwargs:Result())
        result=executor._call(['scripts/campaign-join.py','status'],'needs-human-fixture')
        self.assertEqual(result['state'],'needs_human')
        self.assertEqual(result['errorCode'],'verification_pending')

    def test_nested_batch_report_preserves_write_and_item_totals(self):
        class Result:
            returncode=0;stdout='{"steps": 2}\n';stderr=''
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir()
            def runner(command,**_kwargs):
                report=Path(command[command.index('--report')+1])
                report.write_text(json.dumps({
                    'steps':[
                        {'result':{'platformWrites':168}},
                        {'result':{'platformWrites':99}},
                    ],
                    'finalSummary':{'total':499},
                }))
                return Result()
            result=SubprocessStageExecutor(root,runner=runner)._call(
                ['scripts/catalog-link-batch.py','--route','campaign'],'nested-batch-fixture')
        self.assertEqual(result['state'],'completed')
        self.assertEqual(result['platformWrites'],267)
        self.assertEqual(result['itemCount'],499)


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
