import json
import os
import sqlite3
import tempfile
import unittest
import sys
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
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

    def test_manual_recovery_runs_before_an_older_scheduled_market(self):
        from lib.operations_workflow import create_run
        self.store.plan('bjn-local-research','br')
        save_setting(self.store,'it','scheduler-priority-setting',0,{'automaticOperationsEnabled':True})
        scheduled=create_run(self.store,market='it',trigger_source='schedule',scheduled_at=NOW-100,
                             sources=['campaign'])
        manual=create_run(self.store,market='br',trigger_source='manual',scheduled_at=NOW,
                          request_id='manual-priority-br',only_stage='oecid',sources=['campaign'])
        executor=FakeExecutor();progress=tick(self.root,now=NOW,executor=executor)
        self.assertEqual(progress['runId'],manual['runId'])
        self.assertEqual(executor.calls,[(manual['runId'],'oecid')])
        self.assertNotEqual(progress['runId'],scheduled['runId'])


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
            payload={'missing':0} if args[0]=='scripts/catalog-names.py' else {'done':1}
            return {'state':'completed','itemCount':1,'complete':True,'platformWrites':0,'payload':payload}
        executor,calls=self.executor(answers)
        self.assertEqual(executor.execute(None,{'market':'br','applicableSources':['campaign']},'taplink_prepare',{'jobs':{}})['state'],'completed')
        self.assertTrue(all('--market' in args and args[args.index('--market')+1]=='br' for args,_ in calls))
        calls.clear();result=executor.execute(None,{'market':'br','applicableSources':['campaign']},'kalodata',{'jobs':{}})
        self.assertEqual((result['state'],result['itemCount']),('completed',1))
        self.assertEqual(calls[0][0][:3],['scripts/leads-run.py','--market','br'])

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
        self.assertEqual([args[:2] for args,_ in calls],[['scripts/campaign-join.py','status'],
                         ['scripts/campaign-join.py','join-all'],['scripts/campaign-collect.py','--max-requests']])

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
            result=executor.execute(None,{'runId':'workflow-test','applicableSources':['selected']},'catalog',{'jobs':{}})
            self.assertEqual((result['state'],result['itemCount']),('completed',31809))
            self.assertIn('--by-category',calls[0][0])
            self.assertEqual(result['platformWrites'],21)
            self.assertEqual([call[0][0] for call in calls],
                             ['scripts/collect-global-opportunity.py','scripts/select-global-products.py','scripts/select-global-products.py',
                              'scripts/select-global-products.py','scripts/sync-cycle-catalog.py'])

    def test_uk_weekly_full_managed_catalog_uses_the_plain_query(self):
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
                  'CREATE TABLE global_source_operator_acceptance(run_id TEXT,accepted_at REAL);')
                db.execute("INSERT INTO global_source_run VALUES('r',?,'accepted_partial',?,1)",(json.dumps({'market':'uk','partitionMode':'category_l1_v1'}),NOW-86400))
                db.execute("INSERT INTO global_source_operator_acceptance VALUES('r',?)",(NOW-86400,))
            executor=SubprocessStageExecutor(root);calls=[]
            executor._call=lambda args,label,timeout=14400:(calls.append((args,label)) or answers(args,label))
            result=executor.execute(None,{'runId':'workflow-uk','market':'uk','applicableSources':['selected']},'catalog',{'jobs':{}})
            self.assertEqual(result['state'],'completed')
            self.assertIn('--market',calls[0][0]);self.assertNotIn('--by-category',calls[0][0])

    def test_category_refresh_returns_after_thirty_days(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'var').mkdir();(root/'config').mkdir()
            (root/'config/operations-policy.json').write_text((ROOT/'config/operations-policy.json').read_text())
            with closing(sqlite3.connect(root/'var/global-source-uk.sqlite')) as db,db:
                db.executescript('CREATE TABLE global_source_run(id TEXT,scope TEXT,state TEXT,updated REAL,identity_unchanged INTEGER);'
                  'CREATE TABLE global_source_operator_acceptance(run_id TEXT,accepted_at REAL);')
                db.execute("INSERT INTO global_source_run VALUES('r',?,'completed',?,1)",(json.dumps({'market':'uk','partitionMode':'category_l1_v1'}),NOW-31*86400))
            self.assertEqual(full_catalog_collection_mode(root,'uk',NOW)['mode'],'category')

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
        executor,calls=self.executor(answers);result=executor.execute(
            None,{'runId':'workflow-test','applicableSources':['selected']},'catalog',{'jobs':{}})
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

    def test_kalodata_counts_completed_video_pids_at_quota(self):
        def answers(args,_label):
            if args[0]=='scripts/leads-run.py':payload={'done':1261}
            elif args[1]=='init':payload={'generationId':'video-generation-test'}
            else:payload={'error':'kalodata_daily_quota_exhausted',
                          'status':{'counts':{'completed':20}}}
            return {'state':'completed','itemCount':0,'complete':True,'platformWrites':0,'payload':payload}
        result=self.executor(answers)[0].execute(
            None,{'applicableSources':['campaign']},'kalodata',{'jobs':{}})
        self.assertEqual(result['state'],'quota_exhausted')
        self.assertEqual(result['itemCount'],1281)
        self.assertEqual(result['scope']['aCompleted'],1261)
        self.assertEqual(result['scope']['bCompleted'],20)

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
